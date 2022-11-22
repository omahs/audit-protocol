from config import settings
from data_models import DAGFinalizerCallback, DAGFinalizerCBEventData, AuditRecordTxEventData, PendingTransaction
from utils.redis_conn import provide_redis_conn
from eth_utils import keccak
from uuid import uuid4
from utils import redis_keys
import time
import httpx
import random
import redis
import string
import logging
import sys
import coloredlogs


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(levelname)-8s %(name)-4s %(asctime)s %(msecs)d %(module)s-%(funcName)s: %(message)s")
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(formatter)
stdout_handler.setLevel(logging.DEBUG)
logger.addHandler(stdout_handler)

stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setFormatter(formatter)
stderr_handler.setLevel(logging.ERROR)
logger.addHandler(stderr_handler)
logger.debug("Initialized logger")

coloredlogs.install(level="DEBUG", logger=logger, stream=sys.stdout)


@provide_redis_conn
def main(redis_conn: redis.Redis):
    # initial clear
    redis_conn.delete(redis_keys.get_pending_transactions_key('simulationRun'))
    beginning_height = 1
    # put in pending tx entries to simulate payload commit to tx manager from 1 to num_blocks
    # last_sent_block = midway through num blocks
    # send finalization callbacks from 1 to (last_sent_block - 1)
    # set last touched block against pending tx entry at `last_sent_block` to -1
    # but finalized height is still (last_sent_block - 1). Simulates that DAG put at IPFS failed at `last_sent_block`
    #
    num_blocks = 20
    details = dict()
    for i in range(beginning_height, beginning_height+num_blocks):
        tx_hash = '0x'+keccak(text=''.join(random.choices(string.ascii_lowercase, k=5))).hex()
        pending_tx_entry = PendingTransaction(
            txHash=tx_hash,
            requestID=str(uuid4()),
            lastTouchedBlock=0,
            event_data=None
        )
        _ = redis_conn.zadd(
            name=redis_keys.get_pending_transactions_key('simulationRun'),
            mapping={pending_tx_entry.json(): i}
        )
        if _:
            details[i] = pending_tx_entry
            logger.debug(
                'Added pending tx entry against height %s : %s', i, pending_tx_entry
            )
    last_sent_block = int(beginning_height+num_blocks/2)
    # send finalization call backs
    for i in range(beginning_height, last_sent_block):
        finalization_cb = DAGFinalizerCallback(
            txHash=details[i].txHash,
            requestID=details[i].requestID,
            event_data=DAGFinalizerCBEventData(
                apiKeyHash='0x'+keccak(text=''.join(random.choices(string.ascii_lowercase, k=5))).hex(),
                tentativeBlockHeight=i,
                projectId='simulationRun',
                snapshotCid=''.join(random.choices(string.ascii_lowercase, k=20)),
                payloadCommitId='0x'+keccak(text=''.join(random.choices(string.ascii_lowercase, k=5))).hex(),
                timestamp=int(time.time())
            )
        )
        req_json = finalization_cb.dict()
        req_json.update({'event_name': 'RecordAppended'})
        r = httpx.post(
            url=f'http://{settings.webhook_listener.host}:{settings.webhook_listener.port}/',
            json=req_json
        )
        details[i].event_data = DAGFinalizerCBEventData.parse_obj(finalization_cb.event_data)
        if r.status_code == 200:
            logger.debug('Published callback to DAG finalizer at height %s : %s', i, finalization_cb)
        else:
            logger.error(
                'Failure publishing callback to DAG finalizer at height %s : %s | Response status: %s',
                i, finalization_cb, r.status_code
            )
            return
        logger.debug('Sleeping...')
        time.sleep(0.5)
    # set last touched block against pending tx entry at `last_sent_block` to -1
    # adapted from dag_utils.update_pending_tx_block_touch since it is an async function
    # first, remove
    redis_conn.zremrangebyscore(
        redis_keys.get_pending_transactions_key('simulationRun'),
        min=last_sent_block,
        max=last_sent_block
    )
    # then, put in new entry
    new_pending_tx_set_entry_obj: PendingTransaction = details[last_sent_block]
    new_pending_tx_set_entry_obj.lastTouchedBlock = -1
    new_pending_tx_set_entry_obj.event_data = AuditRecordTxEventData(
        txHash=new_pending_tx_set_entry_obj.txHash,
        projectId='simulationRun',
        apiKeyHash='0x' + keccak(text=''.join(random.choices(string.ascii_lowercase, k=5))).hex(),
        timestamp=int(time.time()),
        payloadCommitId='0x' + keccak(text=''.join(random.choices(string.ascii_lowercase, k=5))).hex(),
        snapshotCid=''.join(random.choices(string.ascii_lowercase, k=20)),
        tentativeBlockHeight=last_sent_block
    )
    _ = redis_conn.zadd(
        name=redis_keys.get_pending_transactions_key('simulationRun'),
        mapping={new_pending_tx_set_entry_obj.json(): last_sent_block}
    )
    if _:
        logger.info(
            'Updated pending tx entry at height %s so that last touched block = -1 : %s',
            last_sent_block, new_pending_tx_set_entry_obj
        )
    else:
        logger.info(
            'Could not update pending tx entry at height %s so that last touched block = -1 : %s',
            last_sent_block, new_pending_tx_set_entry_obj
        )
    # resume sending callbacks from last sent block+1 to end
    for i in range(last_sent_block+1, beginning_height+num_blocks):
        finalization_cb = DAGFinalizerCallback(
            txHash=details[i].txHash,
            requestID=details[i].requestID,
            event_data=DAGFinalizerCBEventData(
                apiKeyHash='0x' + keccak(text=''.join(random.choices(string.ascii_lowercase, k=5))).hex(),
                tentativeBlockHeight=i,
                projectId='simulationRun',
                snapshotCid=''.join(random.choices(string.ascii_lowercase, k=20)),
                payloadCommitId='0x' + keccak(text=''.join(random.choices(string.ascii_lowercase, k=5))).hex(),
                timestamp=int(time.time())
            )
        )
        r = httpx.post(
            url=f'http://{settings.webhook_listener.host}:{settings.webhook_listener.port}/',
            json=finalization_cb.dict()
        )
        details[i].event_data = DAGFinalizerCBEventData.parse_obj(finalization_cb.event_data)
        if r.status_code == 200:
            logger.debug('Published callback to DAG finalizer at height %s : %s', i, finalization_cb)
        else:
            logger.error(
                'Failure publishing callback to DAG finalizer at height %s : %s | Response status: %s',
                i, finalization_cb, r.status_code
            )
            return
        logger.debug('Sleeping...')
        time.sleep(1)


if __name__ == '__main__':
    main()
