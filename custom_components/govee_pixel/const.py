"""Constants for the Govee Pixel Light integration."""

DOMAIN = "govee_pixel"

CONF_TOKEN = "token"
CONF_CLIENT_ID = "client_id"
CONF_ACCOUNT_ID = "account_id"
CONF_ACCOUNT_TOPIC = "account_topic"
CONF_CODE = "code"
CONF_BASE_URL = "base_url"
CONF_ENCODER = "encoder"
CONF_PLAYBACK = "playback"
CONF_RESUME = "resume"
CONF_REFRESH_MINUTES = "refresh_minutes"
CONF_RECLAIM = "reclaim"
CONF_CONNECTION = "connection"
CONF_TOPIC_PREFIX = "topic_prefix"
CONF_DEVICE_ID = "device_id"
CONF_SKU = "sku"
CONF_SW_VERSION = "sw_version"
CONF_HW_VERSION = "hw_version"

CONNECTION_CLOUD = "cloud"
CONNECTION_LOCAL = "local_mqtt"
CONNECTION_PIXELD = "pixeld"  # custom firmware running pixeld (its own display daemon)
LOCAL_PANEL_SIDE = 32  # H8630: 32x32

PLAYBACK_LIGHT = "light"
PLAYBACK_HA = "home_assistant"
RESUME_REPUSH = "repush"
RESUME_MODE_PACKET = "mode_packet"
DEFAULT_REFRESH_MINUTES = 15
DEFAULT_INTERRUPT_SECONDS = 60
DEFAULT_ITEM_SECONDS = 30

SIGNAL_PREVIEW = "govee_pixel_preview_{}"

SERVICE_SHOW_IMAGE = "show_image"
SERVICE_SHOW_WEATHER = "show_weather"
SERVICE_SHOW_TEXT = "show_text"
SERVICE_SHOW_ANIMATION = "show_animation"
SERVICE_PLAYLIST_ADD = "playlist_add"
SERVICE_PLAYLIST_REMOVE = "playlist_remove"
SERVICE_PLAYLIST_CLEAR = "playlist_clear"
SERVICE_PLAYLIST_RESUME = "playlist_resume"
SERVICE_PLAYLIST_REFRESH = "playlist_refresh"

ATTR_DEVICE_ID = "device_id"
ATTR_IMAGE_PATH = "image_path"
ATTR_IMAGE_URL = "image_url"
ATTR_IMAGE_ENTITY = "image_entity"
ATTR_FIT = "fit"
ATTR_RESAMPLE = "resample"
ATTR_BACKGROUND = "background"
ATTR_FRAME_DURATION = "frame_duration"
ATTR_MAX_FRAMES = "max_frames"
ATTR_WEATHER_ENTITY = "weather_entity"
ATTR_CONDITION = "condition"
ATTR_TEMPERATURE = "temperature"
ATTR_TEMPERATURE_UNIT = "temperature_unit"
ATTR_FORECAST = "forecast"
ATTR_MESSAGE = "message"
ATTR_COLOR = "color"
ATTR_SIZE = "size"
ATTR_ANIMATION = "animation"
ATTR_DURATION = "duration"
ATTR_NAME = "name"
ATTR_ITEM_TYPE = "item_type"
ATTR_OPTIONS = "options"
ATTR_POSITION = "position"

# How long a served effect stays downloadable (the light fetches it within seconds).
EFFECT_TTL = 600
MAX_SOURCE_BYTES = 20 * 1024 * 1024
