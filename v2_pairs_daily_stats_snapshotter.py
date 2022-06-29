from utils import redis_keys
from utils.redis_conn import RedisPool
from utils import helper_functions
from utils.retrieval_utils import retrieve_payload_data
import aioredis
import asyncio
import json
from httpx import AsyncClient, Timeout, Limits
from utils.retrieval_utils import retrieve_block_data
import logging.config
from data_models import uniswapDailyStatsSnapshotZset
import sys

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

def get_nearest_v2_pair_summary_snapshot(list_of_zset_entries, exact_score):
    """
    Get the nearest entry in the zset.
    """
    nearest_score = 0
    nearest_value = ""
    for value, score in list_of_zset_entries:
        score = int(score)
        value = value.decode("utf-8")
        if int(score) == exact_score:
            return value

        elif abs(score - exact_score) < abs(nearest_score - exact_score):
            nearest_score = score
            nearest_value = value

    return nearest_value

def v2_pair_data_unpack(prop):
    prop = prop.replace("US$", "")
    prop = prop.replace(",", "")
    return int(prop)

def link_contract_objs_of_v2_pairs_snapshot(recent_v2_pairs_snapshot, old_v2_pairs_snapshot):
    linked_contract_snapshot = {}
    for new_contract_obj in recent_v2_pairs_snapshot:
        linked_contract_snapshot[new_contract_obj["contractAddress"]] = {
            "recent": new_contract_obj
        }

        for old_contract_obj in old_v2_pairs_snapshot:
            if new_contract_obj["contractAddress"] == old_contract_obj["contractAddress"]:
                linked_contract_snapshot[new_contract_obj["contractAddress"]]["old"] = old_contract_obj

    return linked_contract_snapshot

async def v2_pairs_daily_stats_snapshotter(redis_conn=None):
    try:
        if not redis_conn:
            aioredis_pool = RedisPool()
            await aioredis_pool.populate()
            redis_conn: aioredis.Redis = aioredis_pool.writer_redis_pool
        
        # latest snaphot of v2 pair summary
        latest_pair_summary_snapshot = await redis_conn.zrevrange(
            name=redis_keys.get_uniswap_pair_snapshot_summary_zset(),
            start=0,
            end=0,
            withscores=True
        )
        if len(latest_pair_summary_snapshot) < 1:
            logger.error(f"Error v2 pairs summary snapshot zset doesn't have any entry")
            return
        latest_pair_summary_payload, latest_pair_summary_block_height = latest_pair_summary_snapshot[0]
        latest_pair_summary_payload = json.loads(latest_pair_summary_payload.decode("utf-8"))
        latest_pair_summary_payloadCID = latest_pair_summary_payload.get('cid')
        latest_pair_summary_block_height = int(latest_pair_summary_block_height)


        # latest snapshot of v2 pair daily stats
        pair_daily_stats_latest_snapshot = await redis_conn.zrevrange(
            name=redis_keys.get_uniswap_pair_daily_stats_snapshot_zset(),
            start=0,
            end=0,
            withscores=True
        )
        # parse height or set it to 0
        pair_daily_stats_latest_block_height = 0
        if len(pair_daily_stats_latest_snapshot) > 0:
            _, pair_daily_stats_latest_block_height = pair_daily_stats_latest_snapshot[0]
            pair_daily_stats_latest_block_height = int(pair_daily_stats_latest_block_height)
            
        
        # if current hieght of pair snapshot is greater than height of pair daily stats
        if latest_pair_summary_block_height > pair_daily_stats_latest_block_height:
            latest_pair_summary_timestamp = await redis_conn.zscore(
                name=redis_keys.get_uniswap_pair_snapshot_timestamp_zset(),
                value=latest_pair_summary_payloadCID
            )
            if not latest_pair_summary_timestamp:
                logger.error(f"Error v2 pairs summary timestamp zset doesn't have any entry for payloadCID: {latest_pair_summary_payloadCID}")
                return

            latest_pair_summary_timestamp_payloadCID = latest_pair_summary_payloadCID
            latest_pair_summary_timestamp = int(latest_pair_summary_timestamp)
            

            # evaluate 24h old timestamp
            pair_summary_timestamp_24h = latest_pair_summary_timestamp - 60 * 60 * 24
            list_of_zset_entries = await redis_conn.zrangebyscore(
                name=redis_keys.get_uniswap_pair_snapshot_timestamp_zset(),
                min=pair_summary_timestamp_24h - 60 * 30, # 24h_timestap - 30min
                max=pair_summary_timestamp_24h + 60 * 30, # 24h_timestap + 30min
                withscores=True
            )

            # get exact 24h old payload CID or nearest one
            pair_snapshot_payloadCID_24h = get_nearest_v2_pair_summary_snapshot(
                list_of_zset_entries, pair_summary_timestamp_24h
            )

            if pair_snapshot_payloadCID_24h == "":
                logger.error(f"Error v2 pairs summary snapshots don't have enough data to get 24h old entry, so taking oldest available entry") 
                last_entry_of_summary_snapshot = await redis_conn.zrange(
                    name=redis_keys.get_uniswap_pair_snapshot_timestamp_zset(),
                    start=0,
                    end=0,
                    withscores=True
                )
                if len(last_entry_of_summary_snapshot) < 1:
                    logger.error(f"Error v2 pairs summary snapshots don't have any entry")
                    return

                pair_snapshot_payloadCID_24h, last_entry_timestamp = last_entry_of_summary_snapshot[0]    
                pair_snapshot_payloadCID_24h = pair_snapshot_payloadCID_24h.decode("utf-8")

            # fetch current and 24h old snapshot payload
            dag_block_latest, dag_block_24h = await asyncio.gather(
                retrieve_payload_data(latest_pair_summary_timestamp_payloadCID),
                retrieve_payload_data(pair_snapshot_payloadCID_24h)
            )
            dag_block_latest = json.loads(dag_block_latest).get("data", None) if dag_block_latest else None
            dag_block_24h = json.loads(dag_block_24h).get("data", None) if dag_block_24h else None

            # link each contract obj for current and old snapshot
            linked_contracts_snapshot = link_contract_objs_of_v2_pairs_snapshot(dag_block_latest, dag_block_24h)
            
            # parse common block height from v2 pair summary snapshot (no need validate height across pairs in snapshot)
            common_blockheight_reached = dag_block_latest[0].get("block_height", None)
            if not common_blockheight_reached:
                logger.error(f"Error v2 pairs daily stats snapshotter can't get common block height")
                return

            # evalute change in current and old snapshot values for each contract seperately
            daily_stats_contracts = []
            for addr, contract_obj in linked_contracts_snapshot.items():  
                # init daily stats snapshot
                daily_stats = {
                    "contract": addr,
                    "volume24": { "currentValue": 0, "previousValue": 0, "change": 0},
                    "tvl": { "currentValue": 0, "previousValue": 0, "change": 0},
                    "fees24": { "currentValue": 0, "previousValue": 0, "change": 0},
                    "block_height": 0,
                    "block_timestamp": 0
                }

                daily_stats["volume24"]["currentValue"] += v2_pair_data_unpack(contract_obj["recent"]["volume_24h"])
                daily_stats["volume24"]["previousValue"] += v2_pair_data_unpack(contract_obj["old"]["volume_24h"])
                
                daily_stats["tvl"]["currentValue"] += v2_pair_data_unpack(contract_obj["recent"]["liquidity"])
                daily_stats["tvl"]["previousValue"] += v2_pair_data_unpack(contract_obj["old"]["liquidity"])
                
                daily_stats["fees24"]["currentValue"] += v2_pair_data_unpack(contract_obj["recent"]["fees_24h"])
                daily_stats["fees24"]["previousValue"] += v2_pair_data_unpack(contract_obj["old"]["fees_24h"])
            
                # calculate percentage change
                if daily_stats["volume24"]["previousValue"] != 0: 
                    daily_stats["volume24"]["change"] = daily_stats["volume24"]["currentValue"] - daily_stats["volume24"]["previousValue"]
                    daily_stats["volume24"]["change"] = daily_stats["volume24"]["change"] / daily_stats["volume24"]["previousValue"] * 100
                
                if daily_stats["tvl"]["previousValue"] != 0:
                    daily_stats["tvl"]["change"] = daily_stats["tvl"]["currentValue"] - daily_stats["tvl"]["previousValue"]
                    daily_stats["tvl"]["change"] = daily_stats["tvl"]["change"] / daily_stats["tvl"]["previousValue"] * 100

                if daily_stats["fees24"]["previousValue"] != 0:
                    daily_stats["fees24"]["change"] = daily_stats["fees24"]["currentValue"] - daily_stats["fees24"]["previousValue"]
                    daily_stats["fees24"]["change"] = daily_stats["fees24"]["change"] / daily_stats["fees24"]["previousValue"] * 100

                daily_stats["block_height"] = contract_obj["recent"]["block_height"]
                daily_stats["block_timestamp"] = contract_obj["recent"]["block_timestamp"]

                daily_stats_contracts.append(daily_stats)
        
        else:
            logger.debug(f"v2 pair summary & daily stats snapshots are already in sync with block height")
            return


        if daily_stats_contracts:
            # TODO: make these configurable
            async_httpx_client = AsyncClient(
                timeout=Timeout(timeout=5.0),
                follow_redirects=False,
                limits=Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0)
            )
            summarized_payload = {'data': daily_stats_contracts}
            current_audit_project_block_height = await helper_functions.get_block_height(
                project_id=redis_keys.get_uniswap_pairs_v2_daily_snapshot_project_id(),
                reader_redis_conn=redis_conn
            )
            logger.debug('Sending v2 pairs daily stats payload to audit protocol')
            # send to audit protocol for snapshot to be committed
            try:
                response = await helper_functions.commit_payload(
                    project_id=redis_keys.get_uniswap_pairs_v2_daily_snapshot_project_id(),
                    report_payload=summarized_payload,
                    session=async_httpx_client
                )
            except Exception as e:
                logger.error(
                    'Error while committing pairs daily stats snapshot to audit protocol. '
                    'Exception: %s', e
                )
            else:
                if 'message' in response.keys():
                    logger.error(
                        'Error while committing pairs daily stats snapshot to audit protocol. '
                        'Response status code and other information: %s', response
                    )
                else:
                    wait_for_snapshot_project_new_commit = True
                
        if wait_for_snapshot_project_new_commit:
            waitCycles = 0
            while True:
                # introduce a break condition if something goes wrong and snapshot daily stats does not move ahead
                waitCycles+=1
                if waitCycles > 18: # Wait for 60 seconds after which move ahead as something must have has gone wrong with snapshot daily stats submission
                    logger.debug(f"Waited for {waitCycles} cycles, daily stats project has not moved ahead. Stopped waiting to retry in next cycle.")
                    break
                logger.debug('Waiting for 10 seconds to check if latest v2 pairs daily stats snapshot was committed...')
                await asyncio.sleep(10)
                updated_audit_project_block_height = await helper_functions.get_block_height(
                    redis_keys.get_uniswap_pairs_v2_daily_snapshot_project_id(),
                    reader_redis_conn=redis_conn
                )
                if updated_audit_project_block_height > current_audit_project_block_height:
                    logger.debug(
                        'Audit project height against V2 pairs daily stats snapshot is %s | Moved from %s',
                        updated_audit_project_block_height, current_audit_project_block_height
                    )
                    # get head DAG CID retrieve_block_data
                    head_dag_cid = await helper_functions.get_dag_cid(
                        project_id=redis_keys.get_uniswap_pairs_v2_daily_snapshot_project_id(),
                        block_height=updated_audit_project_block_height,
                        reader_redis_conn=redis_conn
                    )
                    dag_block_payload_prefilled = await retrieve_block_data(
                        block_dag_cid=head_dag_cid,
                        writer_redis_conn=redis_conn,
                        data_flag=1
                    )

                    snapshotZsetEntry = uniswapDailyStatsSnapshotZset(
                        cid=dag_block_payload_prefilled['data']['cid'],
                        txHash=dag_block_payload_prefilled['txHash']
                    )

                    # store in snapshots zset
                    await asyncio.gather(
                        redis_conn.zadd(
                            name=redis_keys.get_uniswap_pair_daily_stats_snapshot_zset(),
                            mapping={snapshotZsetEntry.json(): common_blockheight_reached}),
                        redis_conn.set(
                            name=redis_keys.get_uniswap_pair_daily_stats_payload_at_blockheight(common_blockheight_reached),
                            value=json.dumps(dag_block_payload_prefilled['data']['payload']),
                            ex=1800  # TTL of 30 minutes?
                        )
                    )

                    #prune zset
                    block_height_zset_len = await redis_conn.zcard(name=redis_keys.get_uniswap_pair_daily_stats_snapshot_zset())
                    
                    if block_height_zset_len > 20:
                        _ = await redis_conn.zremrangebyrank(
                            name=redis_keys.get_uniswap_pair_daily_stats_snapshot_zset(),
                            min=0,
                            max=-1 * (block_height_zset_len - 20) + 1
                        )
                        logger.debug('Pruned pairs daily stats CID zset by %s elements', _)

                    logger.debug('V2 pairs daily stats snapshot updated...')

                    break

        return ""

    except Exception as e:
        logger.error(f"Error at V2 pair data: {str(e)}", exc_info=True)


if __name__ == '__main__':
    # loop = asyncio.get_event_loop()
    # data = loop.run_until_complete(
    #     v2_pairs_daily_stats_snapshotter()
    # )

    # print(f"\n\n{data}\n")
    pass