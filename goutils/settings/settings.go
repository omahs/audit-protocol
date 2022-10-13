package settings

import (
	"encoding/json"
	"math"
	"os"

	log "github.com/sirupsen/logrus"
)

type RateLimiter_ struct {
	Burst          int `json:"burst"`
	RequestsPerSec int `json:"req_per_sec"`
}

type PruningServiceSettings_ struct {
	RunIntervalMins                      int           `json:"run_interval_mins"`
	IPFSRateLimiter                      *RateLimiter_ `json:"ipfs_rate_limit"`
	Concurrency                          int           `json:"concurrency"`
	CARStoragePath                       string        `json:"car_storage_path"`
	PerformArchival                      bool          `json:"perform_archival"`
	PerformIPFSUnPin                     bool          `json:"perform_ipfs_unpin"`
	PruneRedisZsets                      bool          `json:"prune_redis_zsets"`
	OldestProjectIndex                   string        `json:"oldest_project_index"`
	BackUpRedisZSets                     bool          `json:"backup_redis_zsets_to_file"`
	IpfsTimeout                          int           `json:"ipfs_timeout_secs"`
	SummaryProjectsPruneHeightBehindHead int           `json:"summary_projects_prune_height_behind_head"`
	PruningHeightBehindOldestIndex       int           `json:"pruning_height_behind_oldest_index"`
	Web3Storage                          struct {
		TimeoutSecs int           `json:"timeout_secs"`
		RateLimit   *RateLimiter_ `json:"rate_limit"`
	} `json:"web3_storage"`
}

type _DagVerifierSettings_ struct {
	SlackNotifyURL               string        `json:"slack_notify_URL"`
	RunIntervalSecs              int           `json:"run_interval_secs"`
	SuppressNotificationTimeSecs int64         `json:"suppress_notification_for_secs"`
	SummaryProjectsToTrack       []string      `json:"additional_projects_to_track_prefixes"`
	IPFSRateLimiter              *RateLimiter_ `json:"ipfs_rate_limit,omitempty"`
	Concurrency                  int           `json:"concurrency"`
	RedisPoolSize                int           `json:"redis_pool_size"`
	PruningVerification          bool          `json:"pruning_verification"`
}

//TODO: Move settings into a common package to be used by all go services under audit-protocol.
type SettingsObj struct {
	Host            string `json:"host"`
	Port            int    `json:"port"`
	WebhookListener struct {
		Host        string        `json:"host"`
		Port        int           `json:"port"`
		RateLimiter *RateLimiter_ `json:"rate_limit,omitempty"`
	} `json:"webhook_listener"`
	IpfsURL          string `json:"ipfs_url"`
	SnapshotInterval int    `json:"snapshot_interval"`
	Rlimit           struct {
		FileDescriptors int `json:"file_descriptors"`
	} `json:"rlimit"`
	Rabbitmq struct {
		User     string `json:"user"`
		Password string `json:"password"`
		Host     string `json:"host"`
		Port     int    `json:"port"`
		Setup    struct {
			Core struct {
				Exchange string `json:"exchange"`
			} `json:"core"`
			Queues struct {
				CommitPayloads struct {
					QueueNamePrefix  string `json:"queue_name_prefix"`
					RoutingKeyPrefix string `json:"routing_key_prefix"`
				} `json:"commit-payloads"`
				DiffRequests struct {
					QueueNamePrefix  string `json:"queue_name_prefix"`
					RoutingKeyPrefix string `json:"routing_key_prefix"`
				} `json:"diff-requests"`
			} `json:"queues"`
		} `json:"setup"`
	} `json:"rabbitmq"`
	ContractCallBackend   string        `json:"contract_call_backend"`
	ContractRateLimiter   *RateLimiter_ `json:"contract_rate_limit,omitempty"`
	RetryCount            *int          `json:"retry_count"`
	RetryIntervalSecs     int           `json:"retry_interval_secs"`
	HttpClientTimeoutSecs int           `json:"http_client_timeout_secs"`
	RPCMatic              string        `json:"rpc_matic"`
	ContractAddresses     struct {
		IuniswapV2Factory string `json:"iuniswap_v2_factory"`
		IuniswapV2Router  string `json:"iuniswap_v2_router"`
		IuniswapV2Pair    string `json:"iuniswap_v2_pair"`
		USDT              string `json:"USDT"`
		DAI               string `json:"DAI"`
		USDC              string `json:"USDC"`
		WETH              string `json:"WETH"`
		MAKER             string `json:"MAKER"`
		WETHUSDT          string `json:"WETH-USDT"`
	} `json:"contract_addresses"`
	MetadataCache string `json:"metadata_cache"`
	DagTableName  string `json:"dag_table_name"`
	Seed          string `json:"seed"`
	Redis         struct {
		Host     string `json:"host"`
		Port     int    `json:"port"`
		Db       int    `json:"db"`
		Password string `json:"password"`
	} `json:"redis"`
	RedisReader struct {
		Host     string `json:"host"`
		Port     int    `json:"port"`
		Db       int    `json:"db"`
		Password string `json:"password"`
	} `json:"redis_reader"`
	TableNames struct {
		APIKeys           string `json:"api_keys"`
		AccountingRecords string `json:"accounting_records"`
		RetreivalsSingle  string `json:"retreivals_single"`
		RetreivalsBulk    string `json:"retreivals_bulk"`
	} `json:"table_names"`
	CleanupServiceInterval   int    `json:"cleanup_service_interval"`
	AuditContract            string `json:"audit_contract"`
	AppName                  string `json:"app_name"`
	PowergateClientAddr      string `json:"powergate_client_addr"`
	MaxIpfsBlocks            int    `json:"max_ipfs_blocks"`
	MaxPendingPayloadCommits int    `json:"max_pending_payload_commits"`
	BlockStorage             string `json:"block_storage"`
	PayloadStorage           string `json:"payload_storage"`
	ContainerHeight          int    `json:"container_height"`
	BloomFilterSettings      struct {
		MaxElements int         `json:"max_elements"`
		ErrorRate   float64     `json:"error_rate"`
		Filename    interface{} `json:"filename"`
	} `json:"bloom_filter_settings"`
	PayloadCommitInterval      int           `json:"payload_commit_interval"`
	PayloadCommitConcurrency   int           `json:"payload_commit_concurrency"`
	PruningServiceInterval     int           `json:"pruning_service_interval"`
	RetrievalServiceInterval   int           `json:"retrieval_service_interval"`
	DealWatcherServiceInterval int           `json:"deal_watcher_service_interval"`
	BackupTargets              []string      `json:"backup_targets"`
	MaxPayloadCommits          int           `json:"max_payload_commits"`
	UnpinMode                  string        `json:"unpin_mode"`
	MaxPendingEvents           int           `json:"max_pending_events"`
	IpfsTimeout                int           `json:"ipfs_timeout"`
	IPFSRateLimiter            *RateLimiter_ `json:"ipfs_rate_limit,omitempty"`
	SpanExpireTimeout          int           `json:"span_expire_timeout"`
	APIKey                     string        `json:"api_key"`
	AiohtttpTimeouts           struct {
		SockRead    int `json:"sock_read"`
		SockConnect int `json:"sock_connect"`
		Connect     int `json:"connect"`
	} `json:"aiohtttp_timeouts"`
	Web3Storage struct {
		URL             string        `json:"url"`
		APIToken        string        `json:"api_token"`
		TimeoutSecs     int           `json:"timeout_secs"`
		MaxIdleConns    int           `json:"max_idle_conns"`
		IdleConnTimeout int           `json:"idle_conn_timeout"`
		RateLimiter     *RateLimiter_ `json:"rate_limit,omitempty"`
		UploadURLSuffix string        `json:"upload_url_suffix"`
	} `json:"web3_storage"`
	DagVerifierSettings    _DagVerifierSettings_    `json:"dag_verifier"`
	PruningServiceSettings *PruningServiceSettings_ `json:"pruning"`
	UseConsensus           bool                     `json:"use_consensus"`
	ConsensusConfig        ConsensusConfig_         `json:"consensus_config"`
	InstanceId             string                   `json:"instance_id"`
}

type ConsensusConfig_ struct {
	ServiceURL          string        `json:"service_url"`
	RateLimiter         *RateLimiter_ `json:"rate_limit"`
	TimeoutSecs         int           `json:"timeout_secs"`
	MaxIdleConns        int           `json:"max_idle_conns"`
	IdleConnTimeout     int           `json:"idle_conn_timeout"`
	FinalizationWaiTime int64         `json:"finalization_wait_time_secs"`
}

func ParseSettings(settingsFile string) *SettingsObj {
	var settingsObj SettingsObj
	log.Info("Reading Settings:", settingsFile)
	data, err := os.ReadFile(settingsFile)
	if err != nil {
		log.Error("Cannot read the file:", err)
		panic(err)
	}

	log.Debug("Settings json data is", string(data))
	err = json.Unmarshal(data, &settingsObj)
	if err != nil {
		log.Error("Cannot unmarshal the settings json ", err)
		panic(err)
	}
	SetDefaults(&settingsObj)
	log.Infof("Final Settings Object being used %+v", settingsObj)
	return &settingsObj
}

func SetDefaults(settingsObj *SettingsObj) {
	//Set defaults for settings that are not configured.
	if settingsObj.RetryCount == nil {
		settingsObj.RetryCount = new(int)
		*settingsObj.RetryCount = 10
	} else if *settingsObj.RetryCount == 0 { //This means retry unlimited number of times.
		*settingsObj.RetryCount = math.MaxInt
	}
	if settingsObj.RetryIntervalSecs == 0 {
		settingsObj.RetryIntervalSecs = 5
	}
	if settingsObj.HttpClientTimeoutSecs == 0 {
		settingsObj.HttpClientTimeoutSecs = 10
	}
	if settingsObj.PayloadCommitConcurrency == 0 {
		settingsObj.PayloadCommitConcurrency = 20
	}
	if settingsObj.Web3Storage.UploadURLSuffix == "" {
		settingsObj.Web3Storage.UploadURLSuffix = "/upload"
	}
	if settingsObj.DagVerifierSettings.RunIntervalSecs == 0 {
		settingsObj.DagVerifierSettings.RunIntervalSecs = 300
	}
	if settingsObj.DagVerifierSettings.SlackNotifyURL == "" {
		log.Warnf("Slack Notification URL is not set, any issues observed by this service will not be notified.")
	}
	if settingsObj.DagVerifierSettings.SuppressNotificationTimeSecs == 0 {
		settingsObj.DagVerifierSettings.SuppressNotificationTimeSecs = 1800
	}
	if settingsObj.DagVerifierSettings.Concurrency == 0 {
		settingsObj.DagVerifierSettings.Concurrency = 10
	}
}