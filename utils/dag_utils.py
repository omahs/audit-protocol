from config import settings
from maticvigil.EVCore import EVCore
from utils.ipfs_async import client as ipfs_client
from utils import redis_keys
from utils import helper_functions
from data_models import PendingTransaction, DAGBlock
from typing import Tuple
import async_timeout
import asyncio
import json
import io
import logging
import aioredis
import hmac

logger = logging.getLogger(__name__)
logger.setLevel(level="DEBUG")

evc = EVCore(verbose=True)


def check_signature(core_payload, signature):
    """
    Given the core_payload, check the signature generated by the webhook listener
    """
    api_key = evc._api_write_key
    _sign_rebuilt = hmac.new(
        key=api_key.encode('utf-8'),
        msg=json.dumps(core_payload).encode('utf-8'),
        digestmod='sha256'
    ).hexdigest()

    logger.debug("Signature from header: ")
    logger.debug(signature)
    logger.debug("Signature rebuilt from core payload: ")
    logger.debug(_sign_rebuilt)

    return _sign_rebuilt == signature


async def update_pending_tx_block_touch(
        pending_tx_set_entry: bytes,
        touched_at_block: int,
        project_id: str,
        tentative_block_height: int,
        writer_redis_conn: aioredis.Redis,
        event_data: dict = None
):
    # update last touched block in pending txs key to ensure it is known this guy is already home
    # first, remove
    r_ = await writer_redis_conn.zrem(
        redis_keys.get_pending_transactions_key(project_id), pending_tx_set_entry
    )
    # then, put in new entry
    new_pending_tx_set_entry_obj: PendingTransaction = PendingTransaction.parse_raw(pending_tx_set_entry)
    new_pending_tx_set_entry_obj.lastTouchedBlock = touched_at_block
    if event_data:
        new_pending_tx_set_entry_obj.event_data = event_data
    r__ = await writer_redis_conn.zadd(
        name=redis_keys.get_pending_transactions_key(project_id),
        mapping={new_pending_tx_set_entry_obj.json(): tentative_block_height}
    )
    return {'status': bool(r_) and bool(r__), 'results': {'zrem': r_, 'zadd': r__}}


async def save_event_data(event_data: dict, pending_tx_set_entry: bytes, writer_redis_conn: aioredis.Redis):
    """
        - Given event_data, save the txHash, timestamp, projectId, snapshotCid, tentativeBlockHeight
        onto a redis HashTable with key: eventData:{payloadCommitId}
        - Update state in pending tx
        - And then add the payload_commit_id to a zset with key: projectId:{projectId}:pendingBlocks
        with score being the tentativeBlockHeight
    """

    fields = {
        'txHash': event_data['txHash'],
        'projectId': event_data['event_data']['projectId'],
        'timestamp': event_data['event_data']['timestamp'],
        'snapshotCid': event_data['event_data']['snapshotCid'],
        'payloadCommitId': event_data['event_data']['payloadCommitId'],
        'apiKeyHash': event_data['event_data']['apiKeyHash'],
        'tentativeBlockHeight': event_data['event_data']['tentativeBlockHeight']
    }

    return await update_pending_tx_block_touch(
        pending_tx_set_entry=pending_tx_set_entry,
        touched_at_block=-1,
        project_id=fields['projectId'],
        tentative_block_height=int(event_data['event_data']['tentativeBlockHeight']),
        event_data=fields,
        writer_redis_conn=writer_redis_conn
    )


async def get_dag_block(dag_cid: str):
    e_obj = None
    try:
        async with async_timeout.timeout(settings.ipfs_timeout) as cm:
            try:
                dag = await ipfs_client.dag.get(dag_cid)
            except Exception as e:
                e_obj = e
    except (asyncio.exceptions.CancelledError, asyncio.exceptions.TimeoutError) as err:
        e_obj = err

    if e_obj or cm.expired:
        return {}

    return dag.as_json()


async def put_dag_block(dag_json: str):
    dag_json = dag_json.encode('utf-8')
    out = await ipfs_client.dag.put(io.BytesIO(dag_json), pin=True)
    dag_cid = out.as_json()['Cid']['/']

    return dag_cid


async def get_payload(payload_cid: str):
    """ Given the payload cid, retrieve the payload. """
    e_obj = None
    payload = ""
    try:
        async with async_timeout.timeout(settings.ipfs_timeout) as cm:
            try:
                payload = await ipfs_client.cat(cid=payload_cid)
            except Exception as e:
                e_obj = e
    except (asyncio.exceptions.CancelledError, asyncio.exceptions.TimeoutError) as err:
        e_obj = err

    if e_obj or cm.expired:
        logger.error(e_obj, exc_info=True)
        return ""

    if isinstance(payload, bytes):
        payload = payload.decode('utf-8')

    return payload


async def create_dag_block(
        tx_hash: str,
        project_id: str,
        tentative_block_height: int,
        payload_cid: str,
        timestamp: int,
        reader_redis_conn: aioredis.Redis,
        writer_redis_conn: aioredis.Redis,
) -> Tuple[str, DAGBlock]:
    """ Get the last dag cid using the tentativeBlockHeight"""
    last_dag_cid = await helper_functions.get_dag_cid(
        project_id=project_id,
        block_height=tentative_block_height - 1,
        reader_redis_conn=reader_redis_conn
    )

    """ Fill up the dag """
    dag = DAGBlock(
        height=tentative_block_height,
        prevCid=last_dag_cid,
        data=dict(cid=payload_cid, type='HOT_IPFS'),
        txHash=tx_hash,
        timestamp=timestamp
    )

    logger.debug("DAG created: ")
    logger.debug(dag)

    """ Convert dag structure to json and put it on ipfs dag """
    try:
        dag_cid = await put_dag_block(dag.json())
    except Exception as e:
        logger.error("Failed to put dag block on ipfs: %s | Exception: %s", dag, e, exc_info=True)
        raise

    """ Update redis keys """
    last_dag_cid_key = redis_keys.get_last_dag_cid_key(project_id)
    _ = await writer_redis_conn.set(last_dag_cid_key, dag_cid)

    _ = await writer_redis_conn.zadd(
        name=redis_keys.get_dag_cids_key(project_id),
        mapping={dag_cid: tentative_block_height}
    )

    block_height_key = redis_keys.get_block_height_key(project_id=project_id)
    _ = await writer_redis_conn.set(block_height_key, tentative_block_height)

    return dag_cid, dag


async def discard_event(
        project_id: str,
        payload_commit_id: str,
        payload_cid: str,
        tx_hash: str,
        tentative_block_height: int,
        writer_redis_conn: aioredis.Redis
):
    redis_output = []
    d_r = await clear_payload_commit_data(
        project_id=project_id,
        payload_commit_id=payload_commit_id,
        tx_hash=tx_hash,
        tentative_height_pending_tx_entry=tentative_block_height,
        writer_redis_conn=writer_redis_conn
    )
    redis_output.extend(d_r)

    # Delete the payload cid from the list of payloadCids
    # out = await writer_redis_conn.zrem(
    #     key=redis_keys.get_payload_cids_key(project_id),
    #     member=payload_cid
    # )
    # redis_output.append(out)

    # Add the transaction Hash to discarded Transactions
    out = await writer_redis_conn.zadd(
        name=redis_keys.get_discarded_transactions_key(project_id),
        mapping={tx_hash: tentative_block_height}
    )
    redis_output.append(out)

    return redis_output


async def clear_payload_commit_data(
        project_id: str,
        payload_commit_id: str,
        tx_hash: str,
        tentative_height_pending_tx_entry: int,
        writer_redis_conn: aioredis.Redis
):
    """
    This function will be called once a dag block creation is successful to
    clear up all the transient, temporary redis keys associated with that
    particular dag block, since these key will not be needed anymore
    once the dag block has been created successfully.
        - Clear Event Data
        - Remove the tagged transaction hash entry from pendingTransactions set, by its tentative height score
        - Remove the payload_commit_id from pendingBlocks
        - Delete the payload_commit_data
    """
    deletion_result = []

    # remove tx_hash from list of pending transactions
    out = await writer_redis_conn.zremrangebyscore(
        name=redis_keys.get_pending_transactions_key(project_id=project_id),
        min=tentative_height_pending_tx_entry,
        max=tentative_height_pending_tx_entry
    )
    deletion_result.append(out)

    deletion_result.append(out)

    # delete the payload commit id data
    out = await writer_redis_conn.delete(
        redis_keys.get_payload_commit_key(payload_commit_id=payload_commit_id)
    )
    deletion_result.append(out)

    return deletion_result


async def clear_payload_commit_processing_logs(
        project_id: str,
        payload_commit_id: str,
        writer_redis_conn: aioredis.Redis
):
    _ = await writer_redis_conn.delete(redis_keys.get_payload_commit_id_process_logs_zset_key(
        project_id=project_id, payload_commit_id=payload_commit_id
    )
    )
    return _