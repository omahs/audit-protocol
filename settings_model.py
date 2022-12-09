from pydantic import BaseModel, validator
from typing import Union, List, Optional
import json


class ContractAddresses(BaseModel):
    iuniswap_v2_factory: str
    iuniswap_v2_router: str
    iuniswap_v2_pair: str
    USDT: str
    DAI: str
    USDC: str
    WETH: str
    MAKER: str


class WebhookListener(BaseModel):
    host: str
    port: int
    validate_header_sig: bool = False
    keepalive_secs: int = 600
    redis_lock_lifetime: int


class HTTPClientConnection(BaseModel):
    sock_read: int
    sock_connect: int
    connect: int


class RedisConfig(BaseModel):
    host: str
    port: int
    db: int
    password: Optional[str]


class RabbitMQConfig(BaseModel):
    user: str
    password: str
    host: str
    port: int
    setup: dict


class TableNames(BaseModel):
    api_keys: str
    accounting_records: str
    retreivals_single: str
    retreivals_bulk: str


class PruneSettings(BaseModel):
    segment_size: int = 700

class Settings(BaseModel):
    host: str
    port: str
    keepalive_secs: int = 600
    rlimit: dict
    ipfs_url: str
    ipfs_reader_url: str
    rabbitmq: RabbitMQConfig
    seed: str
    audit_contract: str
    contract_call_backend: str
    local_cache_path: str
    pruning: PruneSettings
    ipfs_timeout: int
    aiohtttp_timeouts: Union[HTTPClientConnection, dict]
    webhook_listener: Union[WebhookListener, dict]
    redis: Union[RedisConfig, dict]
    redis_reader: Union[RedisConfig, dict]
    contract_addresses: Union[ContractAddresses, dict]
    calculate_diff: bool
    rpc_url: str