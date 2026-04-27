from typing import List, Optional
from pydantic import BaseModel, Field


# ─── AUTH ─────────────────────────────────────────────────────
class RegisterInput(BaseModel):
    email: str
    password: str
    name: str
    role: str = "admin"
    company_name: str = ""
    company_industry: str = ""
    company_size: str = ""
    phone: str = ""
    timezone: str = "UTC"


class LoginInput(BaseModel):
    email: str
    password: str


class PasswordChangeInput(BaseModel):
    new_password: str


class AccountDeletionVerificationRequest(BaseModel):
    method: str = "email"


class AccountDeletionConfirmInput(BaseModel):
    method: str
    current_password: Optional[str] = None
    verification_code: Optional[str] = None


# ─── USERS ────────────────────────────────────────────────────
class UserCreateInput(BaseModel):
    name: str
    email: str
    role: str = "company_agent"
    sub_role: str = ""
    status: str = "active"
    temporary_password: Optional[str] = None


class PersonalSettingsUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    mobile_number: Optional[str] = None
    avatar: Optional[str] = None
    website_address: Optional[str] = None
    address_info: Optional[str] = None
    locale_information: Optional[str] = None
    timezone: Optional[str] = None
    preferred_language: Optional[str] = None
    date_format: Optional[str] = None
    currency: Optional[str] = None


# ─── CONVERSATIONS ────────────────────────────────────────────
class ConversationCreate(BaseModel):
    customer_id: str
    channel: str = "web_chat"
    subject: str = ""


class MessageCreate(BaseModel):
    content: str
    sender_type: str = "agent"
    attachments: list = []


class MessageUpdate(BaseModel):
    content: str


class ContactConversationStart(BaseModel):
    name: str
    phone: str
    channel: str = "web_chat"
    source: str = "profile_card"


class OutboundConversationStart(BaseModel):
    channel: str
    name: str
    phone: str = ""
    recipient_id: str = ""
    initial_message: str
    source: str = "inbox_outbound"


# ─── LEADS ────────────────────────────────────────────────────
class LeadCreate(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    customer_company_name: str = Field(default="", alias="company")
    source: str = "web_chat"
    status: str = "new"
    notes: str = ""


class LeadUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    customer_company_name: Optional[str] = Field(default=None, alias="company")
    source: Optional[str] = None
    status: Optional[str] = None
    score: Optional[int] = None
    grade: Optional[str] = None
    notes: Optional[str] = None
    assigned_to: Optional[str] = None


# ─── CUSTOMERS ────────────────────────────────────────────────
class CustomerCreate(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    customer_company_name: str = Field(default="", alias="company")
    channels: list = []
    tags: list = []
    segment: str = "general"


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    customer_company_name: Optional[str] = Field(default=None, alias="company")
    channels: Optional[list] = None
    tags: Optional[list] = None
    segment: Optional[str] = None


class CustomerProfileUpdate(BaseModel):
    preferences: dict = {}
    behavioral_data: dict = {}
    engagement_level: str = "general"


class PurchaseCreate(BaseModel):
    customer_id: str
    amount: float = Field(default=0, ge=0)
    currency: str = "USD"
    product_category: str = "general"
    product_details: dict = {}
    purchase_date: Optional[str] = None


# ─── TICKETS ──────────────────────────────────────────────────
class TicketCreate(BaseModel):
    conversation_id: str = ""
    customer_id: str = ""
    subject: str
    description: str = ""
    priority: str = "medium"
    category: str = "general"


class TicketUpdate(BaseModel):
    subject: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    resolution: Optional[str] = None


# ─── KNOWLEDGE BASE ───────────────────────────────────────────
class KBDocCreate(BaseModel):
    title: str
    content: str
    category: str = "general"
    tags: list = []


# ─── SETTINGS ─────────────────────────────────────────────────
class ChannelSettingsUpdate(BaseModel):
    channel: str
    enabled: bool = False
    display_name: str = ""
    api_key: str = ""
    api_secret: str = ""
    phone_number: str = ""
    webhook_url: str = ""
    page_id: str = ""
    phone_number_id: str = ""
    access_token: str = ""
    verify_token: str = ""
    extra_config: dict = {}


class TemplateCreate(BaseModel):
    name: str
    content: str
    category: str = "general"
    channel: str = "all"


class NotificationCreate(BaseModel):
    title: str
    body: str = ""
    notification_type: str = "info"
    target_user_id: Optional[str] = None
    action_url: Optional[str] = None


class NotificationSettingsUpdate(BaseModel):
    notify_new_message: bool = True
    notify_new_lead: bool = True
    notify_new_ticket: bool = True
    notify_ticket_updated: bool = True
    notify_conversation_assigned: bool = True
    notify_system_updates: bool = True
    email_digest: bool = False
    email_digest_frequency: str = "daily"


class EmailSendInput(BaseModel):
    to_email: str
    subject: str
    body: str


# ─── AI ───────────────────────────────────────────────────────
class LLMEngineCreate(BaseModel):
    model_name: str
    provider: str
    api_endpoint: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    version: str = "current"


class AIAgentCreate(BaseModel):
    agent_type: str = "support"
    llm_id: str = ""
    mcp_server_id: str = ""
    api_key_ref: str = ""
    provider: str = ""
    version: str = "current"
    configuration: dict = {}
    is_active: bool = True


class TrainingDataCreate(BaseModel):
    input_text: str
    output_text: str
    data_category: str = "general"


class ContextMemoryCreate(BaseModel):
    convo_id: str = ""
    entity_id: str = ""
    entity_type: str = "customer"
    memory_content: str
    memory_type: str = "summary"
    relevance_score: float = 0.5


class AIEmbeddingCreate(BaseModel):
    memory_id: str
    embedding_vector: List[float] = []
    dimension: int = 0
    embedding_model: str = ""


# ─── MCP ──────────────────────────────────────────────────────
class MCPServerCreate(BaseModel):
    endpoint: str
    status: str = "active"
    region: str = ""
    capabilities: dict = {}


class MCPClientCreate(BaseModel):
    server_id: str = ""
    user_id: str = ""
    client_type: str = "internal"
    client_name: str
    platform: str = ""
    version: str = "v1"
    configuration: dict = {}


class APIEndpointCreate(BaseModel):
    server_id: str = ""
    endpoint_path: str
    http_method: str = "POST"
    endpoint_type: str = "generic"
    request_schema: dict = {}
    response_schema: dict = {}
    is_active: bool = True


class ClientRequestCreate(BaseModel):
    client_id: str
    endpoint_id: str = ""
    convo_id: str = ""
    message_id: str = ""
    request_payload: dict = {}
    response_payload: dict = {}
    response_status: int = 200


class WebhookHandlerCreate(BaseModel):
    client_id: str = ""
    platform: str
    webhook_url: str
    verification_token_ref: str = ""
    handler_config: dict = {}
    is_active: bool = True


# ─── ANALYTICS ────────────────────────────────────────────────
class AnalyticsReportCreate(BaseModel):
    report_type: str
    period: str
    report_data: dict = {}


class MetricCreate(BaseModel):
    metric_name: str
    metric_value: float = 0
    unit: str = "count"


# ─── PRODUCTS / FAQS ──────────────────────────────────────────
class ProductCreate(BaseModel):
    name: str
    product_title: str = ""
    description: str = ""
    price: str = ""
    price_currency: str = "USD"
    category: str = "general"
    product_type: str = "standard"
    images: list[str] = Field(default_factory=list, max_length=3)
    features: list[str] = Field(default_factory=list)


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    product_title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[str] = None
    price_currency: Optional[str] = None
    category: Optional[str] = None
    product_type: Optional[str] = None
    status: Optional[str] = None
    images: Optional[list[str]] = Field(default=None, max_length=3)
    features: Optional[list[str]] = None


class ProductBulkUploadRequest(BaseModel):
    items: List[dict] = Field(default_factory=list)
    upsert: bool = True


class ProductDescriptionRequest(BaseModel):
    name: str
    product_title: str = ""
    product_type: str = ""
    category: str = "general"
    price: str = ""
    price_currency: str = "USD"
    images: List[str] = Field(default_factory=list)


class FAQCreate(BaseModel):
    question: str
    answer: str
    category: str = "general"


# ─── ONBOARDING DOCS ──────────────────────────────────────────
class OnboardingDocCreate(BaseModel):
    title: str
    content: str = ""
    category: str = "general"
    tags: list = []
    file_name: str = ""
    file_type: str = ""


# ─── EXTERNAL WEBHOOKS ────────────────────────────────────────
class ExternalPurchaseRecord(BaseModel):
    customer_phone: str
    customer_name: str = ""
    product_name: str = ""
    cost: float = Field(default=0, ge=0)
    currency: str = "USD"
    purchased_at: Optional[str] = None


class ExternalFeedbackRecord(BaseModel):
    customer_phone: str
    customer_name: str = ""
    stars: int = Field(default=5, ge=1, le=5)
    comment: str = ""
    submitted_at: Optional[str] = None
