import aioredis
import json
import logging
import sys
from config import settings
from bloom_filter import BloomFilter
from eth_utils import keccak

from utils import redis_keys
from utils import helper_functions
from utils import dag_utils
from utils.redis_conn import provide_async_reader_conn_inst

retrieval_utils_logger = logging.getLogger(__name__)
retrieval_utils_logger.setLevel(level=logging.DEBUG)
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.DEBUG)
retrieval_utils_logger.addHandler(stdout_handler)


async def check_ipfs_pinned(from_height: int, to_height: int):
    """
        - Given the span, check if these blocks exist of IPFS yet
        or if they have to be retrieved through the container
    """
    pass


def check_intersection(span_a, span_b):
    """
        - Given two spans, check the intersection between them
    """

    set_a = set(range(span_a[0], span_a[1]+1))
    set_b = set(range(span_b[0], span_b[1]+1))
    overlap = set_a & set_b
    result = float(len(overlap)) / len(set_a)
    result = result * 100
    return result


@provide_async_reader_conn_inst
async def check_overlap(
        from_height: int,
        to_height: int,
        project_id: str,
        reader_redis_conn: aioredis.Redis
):
    """
        - Given a span, check its intersection with other spans and find
        the span which intersects the most.
        - If there is no intersection with any of the spans, the return -1
    """

    # Get the list of all spans
    live_span_key = redis_keys.get_live_spans_key(project_id=project_id, span_id="*")
    span_keys = await reader_redis_conn.keys(pattern=live_span_key)
    retrieval_utils_logger.debug(span_keys)
    # Iterate through each span and check the intersection for each span
    # with the given from_height and to_height
    max_overlap = 0.0
    max_span_id = ""
    each_height_spans = {}
    for span_key in span_keys:
        if isinstance(span_key, bytes):
            span_key = span_key.decode('utf-8')

        out = await reader_redis_conn.get(span_key)
        if out:
            try:
                span_data = json.loads(out.decode('utf-8'))
            except json.JSONDecodeError as jerr:
                retrieval_utils_logger.error(jerr, exc_info=True)
                continue
        else:
            continue

        target_span = (span_data.get('fromHeight'), span_data.get('toHeight'))
        try:
            overlap = check_intersection(span_a=(from_height, to_height), span_b=target_span)
        except Exception as e:
            retrieval_utils_logger.debug("Check intersection function failed.. ")
            retrieval_utils_logger.error(e, exc_info=True)
            overlap = 0.0

        if overlap > max_overlap:
            max_overlap = overlap
            max_span_id = span_key.split(':')[-1]

        # Check overlap for each height:
        current_height = from_height
        while current_height <= to_height:
            if each_height_spans.get(current_height) is None:
                try:
                    overlap = check_intersection(span_a=(current_height, current_height), span_b=target_span)
                except Exception as e:
                    retrieval_utils_logger.debug("Check intersection Failed")
                if overlap == 100.0:
                    each_height_spans[current_height] = span_key.split(':')[-1]
            current_height = current_height + 1

    return max_overlap, max_span_id, each_height_spans


async def get_container_id(
        dag_block_height: int,
        dag_cid: str,
        project_id: str,
        reader_redis_conn: aioredis.Redis
):
    """
        - Given the dag_block_height and dag_cid, get the container_id for the container
        which holds this dag_block
    """

    target_containers = await reader_redis_conn.zrangebyscore(
        key=redis_keys.get_containers_created_key(project_id),
        max=settings.container_height * 2 + dag_block_height + 1,
        min=dag_block_height - settings.container_height * 2 - 1
    )
    container_id = None
    container_data = dict()
    for container_id in target_containers:
        """ Get the data for the container """
        container_id = container_id.decode('utf-8')
        container_data_key = redis_keys.get_container_data_key(container_id)
        out = await reader_redis_conn.hgetall(container_data_key)
        container_data = {k.decode('utf-8'): v.decode('utf-8') for k, v in out.items()}
        bloom_filter_settings = json.loads(container_data['bloomFilterSettings'])
        bloom_object = BloomFilter(**bloom_filter_settings)
        if dag_cid in bloom_object:
            break

    return container_id, container_data


async def check_container_cached(
        container_id: str,
        reader_redis_conn: aioredis.Redis
):
    """
        - Given the container_id check if the data for that container is
        cached on redis
    """

    cached_container_key = redis_keys.get_cached_containers_key(container_id)
    out = await reader_redis_conn.exists(key=cached_container_key)
    if out is 1:
        return True
    else:
        return False


@provide_async_reader_conn_inst
async def check_containers(
        from_height,
        to_height,
        project_id: str,
        reader_redis_conn: aioredis.Redis,
        each_height_spans: dict = {},

):
    """
        - Given the from_height and to_height, check for each dag_cid, what container is required
        and if that container is cached
    """

    # Get the dag cid's for the span
    out = await reader_redis_conn.zrangebyscore(
        key=redis_keys.get_dag_cids_key(project_id=project_id),
        max=to_height,
        min=from_height,
        withscores=True
    )

    last_pruned_height = await helper_functions.get_last_pruned_height(project_id=project_id)

    containers_required = []
    cached = {}
    for dag_cid, dag_block_height in out:
        dag_cid = dag_cid.decode('utf-8')

        # Check if the dag_cid is beyond the range of max_ipfs_blocks
        if (dag_block_height > last_pruned_height) or (each_height_spans.get(dag_block_height) is not None):
            # The dag block is safe
            continue
        else:
            container_id, container_data = await get_container_id(
                dag_block_height=dag_block_height,
                dag_cid=dag_cid,
                project_id=project_id,
                reader_redis_conn=reader_redis_conn
            )

            containers_required.append({container_id: container_data})
            is_cached = await check_container_cached(container_id, reader_redis_conn=reader_redis_conn)
            cached[container_id] = is_cached

    return containers_required, cached


async def fetch_from_span(
        from_height: int,
        to_height: int,
        span_id: str,
        project_id: str,
        reader_redis_conn: aioredis.Redis
):
    """
        - Given the span_id and the span, fetch blocks in that range, if
        they exist
    """
    live_span_key = redis_keys.get_live_spans_key(span_id=span_id, project_id=project_id)
    span_data = await reader_redis_conn.get(live_span_key)

    if span_data:
        try:
            span_data = json.loads(span_data)
        except json.JSONDecodeError as jerr:
            retrieval_utils_logger.error(jerr, exc_info=True)
            return -1

    current_height = from_height
    blocks = []
    while current_height <= to_height:
        blocks.append(span_data['dag_blocks'][current_height])
        current_height = current_height + 1

    return blocks


async def save_span(
        from_height: int,
        to_height: int,
        project_id: str,
        dag_blocks: dict,
        writer_redis_conn: aioredis.Redis
):
    """
        - Given the span, save it.
        - Important to assign it a timeout to make sure that key disappears after a
        certain time.
    """
    span_data = {
        'fromHeight': from_height,
        'toHeight': to_height,
        'projectId': project_id
    }
    span_id = keccak(text=json.dumps(span_data)).hex()

    span_data.pop('projectId')
    span_data['dag_blocks'] = dag_blocks
    live_span_key = redis_keys.get_live_spans_key(project_id=project_id, span_id=span_id)
    _ = await writer_redis_conn.set(live_span_key, json.dumps(span_data))
    _ = await writer_redis_conn.expire(live_span_key, timeout=settings.span_expire_timeout)


@provide_async_reader_conn_inst
async def fetch_blocks(
        from_height: int,
        to_height: int,
        project_id: str,
        reader_redis_conn: aioredis.Redis
):
    """
        - Given the from_height and to_height fetch the blocks based on whether there are any spans
        that exists or not
    """

    max_overlap, max_span_id, each_height_spans = await check_overlap(
            project_id=project_id,
            from_height=from_height,
            to_height=to_height,
    )

    current_height = to_height
    dag_blocks = {}
    while current_height >= from_height:
        dag_cid = await helper_functions.get_dag_cid(project_id=project_id, block_height=current_height)
        if each_height_spans.get(current_height) is None:
            dag_cid = await helper_functions.get_dag_cid(project_id=project_id, block_height=current_height)
            dag_block = await dag_utils.get_dag_block(dag_cid)

        else:
            dag_block: list = await fetch_from_span(
                from_height=current_height,
                to_height=current_height,
                span_id=each_height_spans[current_height],
                project_id=project_id,
                reader_redis_conn=reader_redis_conn
            )

        dag_blocks[dag_cid] = dag_block
        current_height = current_height - 1

        retrieval_utils_logger.debug(dag_blocks)

    return dag_blocks


async def get_blocks_from_container(container_id, dag_cids: list):
    """
        Given the dag_cids, get those dag block from the given container
    """
    pass
