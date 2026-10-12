"""Constants for the Govee Pixel Light integration."""

DOMAIN = "govee_pixel"

CONF_TOKEN = "token"
CONF_CLIENT_ID = "client_id"
CONF_ACCOUNT_ID = "account_id"
CONF_ACCOUNT_TOPIC = "account_topic"
CONF_CODE = "code"
CONF_BASE_URL = "base_url"

SERVICE_SHOW_IMAGE = "show_image"
SERVICE_SHOW_WEATHER = "show_weather"
SERVICE_SHOW_TEXT = "show_text"
SERVICE_SHOW_ANIMATION = "show_animation"

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

# How long a served effect stays downloadable (the light fetches it within seconds).
EFFECT_TTL = 600
MAX_SOURCE_BYTES = 20 * 1024 * 1024
