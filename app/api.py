"""
HTTP layer only. Routes translate requests into service calls and results
into responses — no validation, parsing, or persistence logic lives here.
Cross-cutting concerns (logging, error handling, auth) are attached here
via middleware, exception handlers, and a shared dependency rather than
scattered through routes.
"""
import logging
import os
import time
from dataclasses import asdict
from typing import Set

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from .analytics_service import AnalyticsService
from .auth_schemas import Token, UserOut, UserSignup
from .auth_service import AccountConflictError, AuthService
from .csv_reader import CsvHeaderError, CsvReader
from .daily_summary_service import DailySummaryService
from .database import get_db, init_db
from .email_sender import EmailSender
from .error_logger import CsvFileErrorLogger, ErrorLogger
from .logging_config import configure_logging
from .models import User
from .repositories import BookingRepository, SqlAlchemyBookingRepository
from .scheduler import build_email_sender, run_daily_summary_job, start_scheduler, stop_scheduler
from .security import create_access_token, decode_access_token
from .upload_service import BookingUploadService
from .user_repository import SqlAlchemyUserRepository, UserRepository
from .validators import (
    REQUIRED_FIELDS,
    DateFormatValidator,
    DuplicateBookingNoValidator,
    MandatoryFieldsValidator,
    NegativeAmountValidator,
    RowValidator,
)

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Booking CSV Upload API")

# Allows a browser-based frontend (served from any origin — a local file,
# a different port, a static host) to call this API. Wide open for local
# dev; tighten allow_origins to your actual frontend's URL in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    start_scheduler()
    logger.info("Application startup complete")


@app.on_event("shutdown")
def on_shutdown():
    stop_scheduler()


# --- Request logging middleware -----------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    logger.info("Request start: %s %s", request.method, request.url.path)
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "Request end: %s %s status=%d duration=%.1fms",
        request.method, request.url.path, response.status_code, duration_ms,
    )
    return response


# --- Global exception handlers -------------------------------------------

@app.exception_handler(CsvHeaderError)
async def csv_header_error_handler(request: Request, exc: CsvHeaderError):
    logger.warning("CSV header error on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=400, content={"error": "CsvHeaderError", "detail": str(exc)})


@app.exception_handler(AccountConflictError)
async def account_conflict_handler(request: Request, exc: AccountConflictError):
    logger.warning("Signup rejected on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=400, content={"error": type(exc).__name__, "detail": str(exc)})


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.warning(
        "HTTPException on %s: status=%d detail=%s", request.url.path, exc.status_code, exc.detail,
    )
    return JSONResponse(status_code=exc.status_code, content={"error": "HTTPException", "detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning("Request validation error on %s: %s", request.url.path, exc.errors())
    return JSONResponse(status_code=422, content={"error": "ValidationError", "detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "InternalServerError", "detail": "An unexpected error occurred."},
    )


# --- Dependency wiring -----------------------------------------------------

def get_error_logger() -> ErrorLogger:
    return CsvFileErrorLogger()


def get_repository(db: Session = Depends(get_db)) -> BookingRepository:
    return SqlAlchemyBookingRepository(db)


def get_user_repository(db: Session = Depends(get_db)) -> UserRepository:
    return SqlAlchemyUserRepository(db)


def get_auth_service(user_repository: UserRepository = Depends(get_user_repository)) -> AuthService:
    return AuthService(user_repository)


def build_validators(existing_booking_nos: Set[str]) -> list[RowValidator]:
    return [
        MandatoryFieldsValidator(REQUIRED_FIELDS),
        DateFormatValidator(),
        NegativeAmountValidator(),
        DuplicateBookingNoValidator(existing_booking_nos),
    ]


def get_upload_service(
    repository: BookingRepository = Depends(get_repository),
    error_logger: ErrorLogger = Depends(get_error_logger),
) -> BookingUploadService:
    return BookingUploadService(
        repository=repository,
        error_logger=error_logger,
        csv_reader=CsvReader(REQUIRED_FIELDS),
        validator_factory=build_validators,
    )


def get_analytics_service(repository: BookingRepository = Depends(get_repository)) -> AnalyticsService:
    return AnalyticsService(repository)


def get_daily_summary_service(
    analytics_service: AnalyticsService = Depends(get_analytics_service),
) -> DailySummaryService:
    email_sender = build_email_sender()
    if email_sender is None:
        raise HTTPException(
            status_code=503,
            detail="Email is not configured on this server (SMTP_HOST is not set).",
        )
    recipients_raw = os.environ.get("SUMMARY_EMAIL_RECIPIENTS", "")
    recipients = [addr.strip() for addr in recipients_raw.split(",") if addr.strip()]
    if not recipients:
        raise HTTPException(
            status_code=503,
            detail="No recipients configured (SUMMARY_EMAIL_RECIPIENTS is not set).",
        )
    return DailySummaryService(analytics_service, email_sender, recipients)


# --- Auth dependency ---------------------------------------------------
# tokenUrl points Swagger UI's "Authorize" button at /auth/login so you can
# test protected routes directly from /docs, not just via curl/frontends.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    user_repository: UserRepository = Depends(get_user_repository),
) -> User:
    username = decode_access_token(token)
    if username is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = user_repository.get_by_username(username)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# --- Auth routes -------------------------------------------------------

@app.post("/auth/signup", response_model=UserOut, status_code=201)
def signup(payload: UserSignup, auth_service: AuthService = Depends(get_auth_service)):
    user = auth_service.signup(payload.username, payload.email, payload.password)  # AccountConflictError -> global handler
    return user


@app.post("/auth/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    auth_service: AuthService = Depends(get_auth_service),
):
    user = auth_service.authenticate(form_data.username, form_data.password)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(subject=user.username)
    return Token(access_token=access_token)


@app.get("/auth/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


# --- Routes ----------------------------------------------------------------
# Every route below requires a valid bearer token via get_current_user.

@app.post("/upload-csv")
async def upload_csv(
    file: UploadFile = File(...),
    service: BookingUploadService = Depends(get_upload_service),
    current_user: User = Depends(get_current_user),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not valid UTF-8 text")

    result = service.process(file.filename, text)  # CsvHeaderError bubbles to the global handler

    return JSONResponse({
        "filename": result.filename,
        "total_rows_processed": result.total_rows_processed,
        "rows_inserted": result.rows_inserted,
        "rows_rejected": result.rows_rejected,
        "errors_preview": [asdict(e) for e in result.errors[:50]],
        "errors_truncated": len(result.errors) > 50,
        "error_log_file": result.error_log_file,
        "error_log_download_url": f"/upload-logs/{result.error_log_file}" if result.error_log_file else None,
    })


@app.get("/bookings/count")
def bookings_count(
    repository: BookingRepository = Depends(get_repository),
    current_user: User = Depends(get_current_user),
):
    return {"total_bookings_in_db": repository.count()}


@app.get("/analytics/summary")
def analytics_summary(
    service: AnalyticsService = Depends(get_analytics_service),
    current_user: User = Depends(get_current_user),
):
    return service.get_summary()


@app.get("/analytics/revenue-by-country")
def analytics_revenue_by_country(
    service: AnalyticsService = Depends(get_analytics_service),
    current_user: User = Depends(get_current_user),
):
    return {"revenue_by_country": service.get_revenue_by_country()}


@app.get("/analytics/bookings-by-agent")
def analytics_bookings_by_agent(
    service: AnalyticsService = Depends(get_analytics_service),
    current_user: User = Depends(get_current_user),
):
    return {"bookings_by_agent": service.get_bookings_by_agent()}


@app.get("/analytics/monthly-revenue-trend")
def analytics_monthly_revenue_trend(
    service: AnalyticsService = Depends(get_analytics_service),
    current_user: User = Depends(get_current_user),
):
    return {"monthly_revenue_trend": service.get_monthly_revenue_trend()}


@app.post("/analytics/summary-email/send")
def send_summary_email_now(
    current_user: User = Depends(get_current_user),
    service: DailySummaryService = Depends(get_daily_summary_service),
):
    """Sends the daily summary email immediately — for testing or an on-demand resend."""
    service.send_daily_summary()
    return {"message": "Summary email sent."}


@app.get("/upload-logs")
def list_upload_logs(
    error_logger: ErrorLogger = Depends(get_error_logger),
    current_user: User = Depends(get_current_user),
):
    return {"logs": error_logger.list_logs()}


@app.get("/upload-logs/{log_filename}")
def download_upload_log(
    log_filename: str,
    error_logger: ErrorLogger = Depends(get_error_logger),
    current_user: User = Depends(get_current_user),
):
    log_path = error_logger.get_log_path(log_filename)
    if log_path is None:
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(log_path, media_type="text/csv", filename=log_filename)


@app.get("/")
def root():
    # Left unprotected — a plain health/liveness check with no data in it.
    return {"message": "Booking CSV Upload API. POST a CSV to /upload-csv"}
