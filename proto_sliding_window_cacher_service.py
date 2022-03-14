from utils import redis_keys
from utils.ipfs_async import client as ipfs_client
from config import settings
from utils.redis_conn import provide_async_reader_conn_inst, provide_async_writer_conn_inst
from utils import helper_functions, dag_utils
from utils.retrieval_utils import retrieve_block_data
from functools import wraps, partial
import aioredis
import asyncio
import json
import coloredlogs
import logging
import sys

sliding_cacher_logger = logging.getLogger('AuditProtocol|SlidingWindowCachingService')
sliding_cacher_logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(levelname)-8s %(name)-4s %(asctime)s %(msecs)d %(module)s-%(funcName)s: %(message)s")
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(formatter)
stdout_handler.setLevel(logging.DEBUG)
sliding_cacher_logger.addHandler(stdout_handler)

stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setFormatter(formatter)
stderr_handler.setLevel(logging.ERROR)
sliding_cacher_logger.addHandler(stderr_handler)
sliding_cacher_logger.debug("Initialized logger")
# coloredlogs.install(level="DEBUG", logger=sliding_cacher_logger, stream=sys.stdout)


def acquire_bounded_semaphore(fn):
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        sem: asyncio.BoundedSemaphore = kwargs['semaphore']
        await sem.acquire()
        try:
            await fn(*args, **kwargs)
        except:
            pass
        finally:
            sem.release()
    return wrapped


def convert_time_period_str_to_timestamp(time_period_str: str):
    ts_map = {'24h': 24 * 60 * 60, '7d': 7 * 24 * 60 * 60}
    return ts_map.get(time_period_str, 60 * 60)  # 1 hour timestamp returned by default


async def seek_ahead_tail(head: int, tail: int, project_id: str, time_period_ts: int):
    current_height = tail
    head_cid = await helper_functions.get_dag_cid(project_id=project_id, block_height=head)
    head_block = await dag_utils.get_dag_block(head_cid)
    sliding_cacher_logger.debug(
        'Got head block at %s | Project ID : %s | DAG CID: %s \n%s',
        head, project_id, head_cid, head_block
    )
    present_ts = int(head_block['timestamp'])
    sliding_cacher_logger.debug('Head time stamp: %s', present_ts)
    while current_height < head:
        dag_cid = await helper_functions.get_dag_cid(project_id=project_id, block_height=current_height)
        # dag_block = await retrieve_block_data(block_dag_cid=dag_cid, data_flag=1)
        dag_block = await dag_utils.get_dag_block(dag_cid)
        # dag_blocks[dag_cid] = dag_block
        if present_ts - dag_block['timestamp'] <= time_period_ts:
            return current_height
        current_height += 1
    return None


async def find_tail(head: int, project_id: str, time_period_ts: int):
    current_height = 1
    head_cid = await helper_functions.get_dag_cid(project_id=project_id, block_height=head)
    head_block = await dag_utils.get_dag_block(head_cid)
    sliding_cacher_logger.debug(
        'Got head block at %s | Project ID : %s | DAG CID: %s \n%s',
        head, project_id, head_cid, head_block
    )
    present_ts = int(head_block['timestamp'])
    sliding_cacher_logger.debug('Head time stamp: %s', present_ts)
    while current_height < head:
        dag_cid = await helper_functions.get_dag_cid(project_id=project_id, block_height=current_height)
        # dag_block = await retrieve_block_data(block_dag_cid=dag_cid, data_flag=1)
        dag_block = await dag_utils.get_dag_block(dag_cid)
        # dag_blocks[dag_cid] = dag_block
        if present_ts - dag_block['timestamp'] <= time_period_ts:
            return current_height
        current_height += 1
    return None


@acquire_bounded_semaphore
async def build_primary_index(
        project_id: str,
        time_period: str,
        semaphore: asyncio.BoundedSemaphore,
        writer_redis_conn: aioredis.Redis
):
    """
        :param time_period: supported time_period strings as of now:  ['24h', '7d']
    """
    # project_cids_key = redis_keys.get_dag_cids_key(project_id)
    project_height_key = redis_keys.get_block_height_key(project_id)
    max_height = await writer_redis_conn.get(project_height_key)
    # find markers
    # NOTE: every periodic run, the head although is always chosen to be the max height
    #  1. maybe don't store it? 2. or, might be useful state information?
    idx_head_key = redis_keys.get_sliding_window_cache_head_marker(project_id, time_period)
    idx_tail_key = redis_keys.get_sliding_window_cache_tail_marker(project_id, time_period)
    try:
        max_height = int(max_height)
    except:
        sliding_cacher_logger.error('Did not find max block height against project ID: %s', project_id)
        return
    head_marker = max_height
    tail_marker = None
    time_period_ts = convert_time_period_str_to_timestamp(time_period)
    markers = [await writer_redis_conn.get(k) for k in [idx_head_key, idx_tail_key]]
    if not all(markers):
        sliding_cacher_logger.info('Finding %s tail marker for the first time for project %s', time_period, project_id)
        tail_marker = await find_tail(head_marker, project_id, time_period_ts)
        if not tail_marker:
            sliding_cacher_logger.error(
                'not enough blocks against project ID: %s for %s calculation', project_id, time_period
            )
            return
        await writer_redis_conn.set(redis_keys.get_sliding_window_cache_head_marker(project_id, time_period), head_marker)
        await writer_redis_conn.set(redis_keys.get_sliding_window_cache_tail_marker(project_id, time_period), tail_marker)
        sliding_cacher_logger.info(
            'Set %s - %s index for %s data | First run | Project ID: %s',
            head_marker, tail_marker, time_period, project_id
        )
    else:
        tail_marker = int(markers[1])
        tail_ahead = await seek_ahead_tail(head_marker, tail_marker, project_id, time_period_ts)
        if not tail_ahead:
            sliding_cacher_logger.error(
                'not enough blocks against project ID: %s to seek tail ahead for %s calculation | present head: %s',
                project_id, time_period, head_marker
            )
            # do not update markers in cache
            return
        else:
            sliding_cacher_logger.debug(
                'Sought tail ahead to %s from %s | %s data | Project ID: %s',
                tail_ahead, tail_marker, time_period, project_id
            )
            await writer_redis_conn.set(redis_keys.get_sliding_window_cache_head_marker(project_id, time_period),
                                        head_marker)
            await writer_redis_conn.set(redis_keys.get_sliding_window_cache_tail_marker(project_id, time_period),
                                        tail_ahead)
            sliding_cacher_logger.info(
                'Set %s - %s index for %s data | Project ID: %s',
                head_marker, tail_ahead, time_period, project_id
            )


@provide_async_writer_conn_inst
async def build_primary_indexes(writer_redis_conn: aioredis.Redis = None):
    # project ID -> {"series": ['24h', '7d']}
    registered_projects = await writer_redis_conn.hgetall('cache:indexesRequested')
    sliding_cacher_logger.debug('Got registered projects for indexing: ', registered_projects)
    registered_project_ids = [x.decode('utf-8') for x in registered_projects.keys()]
    registered_projects_ts = [json.loads(v)['series'] for v in registered_projects.values()]
    project_id_to_register_series = dict(zip(registered_project_ids, registered_projects_ts))
    tasks = list()
    semaphore = asyncio.BoundedSemaphore(20)
    for project_id, ts_arr in project_id_to_register_series.items():
        for time_period in ts_arr:
            fn = build_primary_index(**{
                'project_id': project_id,
                'time_period': time_period,
                'semaphore': semaphore,
                'writer_redis_conn': writer_redis_conn
            })
            tasks.append(fn)
    await asyncio.gather(*tasks)


async def periodic_retrieval():
    while True:
        await asyncio.gather(
            build_primary_indexes(),
            asyncio.sleep(120)
        )
        sliding_cacher_logger.debug('Finished a cycle of indexing...')


def verifier_crash_cb(fut: asyncio.Future):
    try:
        exc = fut.exception()
    except asyncio.CancelledError:
        # sliding_cacher_logger.error('Respawning task for populating pair contracts, involved tokens and their metadata...')
        t = asyncio.ensure_future(periodic_retrieval())
        t.add_done_callback(verifier_crash_cb)
    except Exception as e:
        sliding_cacher_logger.error('Indexing task crashed')
        sliding_cacher_logger.error(e, exc_info=True)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    f = asyncio.ensure_future(periodic_retrieval())
    f.add_done_callback(verifier_crash_cb)
    try:
        asyncio.get_event_loop().run_until_complete(f)
    except:
        asyncio.get_event_loop().stop()
