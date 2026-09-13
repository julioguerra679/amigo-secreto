"""
Panel de administración: vistas HTML + API JSON.

Autenticación
-------------
* **Navegador**: contraseña -> cookie firmada (`itsdangerous`) con
  `HttpOnly`, `SameSite=Lax` y expiración. `SameSite=Lax` evita que un sitio
  de terceros dispare acciones POST con la sesión del admin (CSRF básico).
* **Scripts / curl**: cabecera `X-Admin-Password`. Cómodo para automatizar
  y para los tests, sin necesidad de mantener cookies.

Las vistas HTML redirigen al login cuando no hay sesión; los endpoints JSON
responden 401. Por eso hay dos guardianes distintos.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.mailer import is_configured as mail_is_configured
from app.mailer import send_credentials
from app.models import Participant
from app.schemas import (
    DrawOut,
    GenerateAssignmentsIn,
    GenerateAssignmentsOut,
    ParticipantOut,
    UploadParticipantsIn,
    UploadParticipantsOut,
    ValidationReport,
)
from app.security import (
    ADMIN_COOKIE_NAME,
    create_admin_session_token,
    is_admin_request,
    require_admin,
    verify_admin_password,
)
from app.services import draws as draws_service
from app.services import participants as participants_service
from app.services.assignment import AssignmentError
from app.templating import render

router = APIRouter(prefix="/admin", tags=["admin"])

# Cache en memoria de las últimas credenciales emitidas.
# Los PIN en claro no se pueden releer de la base de datos (están hasheados),
# así que se conservan aquí SOLO para que el admin pueda copiarlos/descargar
# el CSV justo después de la carga. Se pierde al reiniciar el proceso: es
# deliberado, no queremos PIN en claro persistidos en disco.
_LAST_CREDENTIALS: list = []


# ===========================================================================
# Guardianes
# ===========================================================================
def _guard_html(request: Request, settings: Settings) -> RedirectResponse | None:
    """Devuelve una redirección al login si la sesión no es válida."""
    if is_admin_request(request, settings):
        return None
    return RedirectResponse(url="/admin/login", status_code=303)


def _set_session_cookie(response: Response, settings: Settings) -> None:
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=create_admin_session_token(settings),
        max_age=settings.admin_session_minutes * 60,
        httponly=True,
        samesite="lax",
        # `secure=True` exige HTTPS. Se activa automáticamente si BASE_URL
        # es https, para no romper el desarrollo en http://127.0.0.1.
        secure=settings.public_base_url.startswith("https://"),
        path="/",
    )


# ===========================================================================
# Login / logout (HTML)
# ===========================================================================
@router.get("", include_in_schema=False)
def admin_root(request: Request, settings: Settings = Depends(get_settings)):
    if is_admin_request(request, settings):
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    return RedirectResponse(url="/admin/login", status_code=303)


@router.get("/login", include_in_schema=False)
def login_form(request: Request, error: str | None = None):
    return render(request, "admin_login.html", {"error": error})


@router.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    password: str = Form(...),
    settings: Settings = Depends(get_settings),
):
    if not verify_admin_password(password, settings):
        return render(
            request,
            "admin_login.html",
            {"error": "Contraseña incorrecta."},
        )
    response = RedirectResponse(url="/admin/dashboard", status_code=303)
    _set_session_cookie(response, settings)
    return response


@router.post("/logout", include_in_schema=False)
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
    return response


# ===========================================================================
# Dashboard (HTML)
# ===========================================================================
def _dashboard_context(db: Session, settings: Settings, **extra) -> dict:
    """Contexto común del panel, reutilizado por todas las acciones."""
    draw = draws_service.get_active_draw(db)
    assignments = draws_service.get_assignments(db, draw) if draw else []
    return {
        "participants": participants_service.list_participants(db),
        "draw": draw,
        "assignments": assignments,
        "history": draws_service.get_draw_history(db),
        "credentials": _LAST_CREDENTIALS,
        "mail_configured": mail_is_configured(settings),
        "base_url": settings.public_base_url,
        "message": None,
        "error": None,
        "validation": None,
        **extra,
    }


@router.get("/dashboard", include_in_schema=False)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    redirect = _guard_html(request, settings)
    if redirect:
        return redirect
    return render(request, "admin_dashboard.html", _dashboard_context(db, settings))


@router.post("/participants", include_in_schema=False)
def upload_participants_form(
    request: Request,
    participants_raw: str = Form(""),
    exclusions_raw: str = Form(""),
    send_email: str | None = Form(None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Carga el padrón desde el textarea del panel.

    Se renderiza el panel directamente (sin redirección) porque la respuesta
    contiene los PIN en claro, que solo existen en este instante.
    """
    redirect = _guard_html(request, settings)
    if redirect:
        return redirect

    global _LAST_CREDENTIALS

    try:
        people = participants_service.parse_participants_text(participants_raw)
        rules = participants_service.parse_exclusions_text(exclusions_raw)
        credentials, n_exclusions = participants_service.replace_participants(
            db, people, rules, settings, replace_existing=True
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return render(
            request,
            "admin_dashboard.html",
            _dashboard_context(db, settings, error=str(exc)),
        )

    _LAST_CREDENTIALS = credentials

    message = (
        f"Se cargaron {len(credentials)} participantes "
        f"y {n_exclusions} exclusión(es). "
        "Los sorteos anteriores se eliminaron: genera uno nuevo."
    )
    if send_email:
        report = send_credentials(credentials, settings)
        message += " " + report.summary
        if report.errors:
            message += " Errores: " + "; ".join(report.errors[:3])

    return render(
        request,
        "admin_dashboard.html",
        _dashboard_context(db, settings, message=message),
    )


@router.post("/draw", include_in_schema=False)
def generate_draw_form(
    request: Request,
    seed: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Genera (o regenera) el sorteo desde el panel."""
    redirect = _guard_html(request, settings)
    if redirect:
        return redirect

    try:
        draw, result = draws_service.create_draw(
            db, seed=seed.strip() or None, notes=notes.strip() or None
        )
        db.commit()
    except (draws_service.DrawServiceError, AssignmentError) as exc:
        db.rollback()
        return render(
            request,
            "admin_dashboard.html",
            _dashboard_context(db, settings, error=str(exc)),
        )

    report = draws_service.validate_draw(db, draw)
    message = (
        f"Sorteo #{draw.id} generado con {draw.participant_count} participantes "
        f"(semilla: {draw.seed}). "
        + ("Validación: correcta ✅" if report.ok else "Validación: con avisos ⚠️")
    )
    return render(
        request,
        "admin_dashboard.html",
        _dashboard_context(db, settings, message=message, validation=report),
    )


@router.post("/validate", include_in_schema=False)
def validate_form(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Audita el sorteo activo y muestra el informe en el panel."""
    redirect = _guard_html(request, settings)
    if redirect:
        return redirect

    draw = draws_service.get_active_draw(db)
    if draw is None:
        return render(
            request,
            "admin_dashboard.html",
            _dashboard_context(db, settings, error="Todavía no hay ningún sorteo."),
        )

    report = draws_service.validate_draw(db, draw)
    return render(
        request,
        "admin_dashboard.html",
        _dashboard_context(
            db,
            settings,
            validation=report,
            message=(
                "Validación completada: el sorteo es correcto ✅"
                if report.ok
                else "Validación completada: se encontraron problemas ⚠️"
            ),
        ),
    )


@router.post("/participants/{participant_id}/reset", include_in_schema=False)
def reset_credentials_form(
    participant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Emite PIN + link nuevos para un participante (invalida los previos)."""
    redirect = _guard_html(request, settings)
    if redirect:
        return redirect

    global _LAST_CREDENTIALS

    participant = db.get(Participant, participant_id)
    if participant is None:
        return render(
            request,
            "admin_dashboard.html",
            _dashboard_context(db, settings, error="Participante no encontrado."),
        )

    cred = participants_service.regenerate_credentials(db, participant, settings)
    db.commit()
    _LAST_CREDENTIALS = [cred]

    return render(
        request,
        "admin_dashboard.html",
        _dashboard_context(
            db,
            settings,
            message=(
                f"Nuevas credenciales para {cred.name}. "
                "El link anterior ya no funciona."
            ),
        ),
    )


@router.get("/credentials.csv", include_in_schema=False)
def download_credentials(
    request: Request, settings: Settings = Depends(get_settings)
):
    """Descarga el CSV con los links y PIN recién emitidos."""
    redirect = _guard_html(request, settings)
    if redirect:
        return redirect
    if not _LAST_CREDENTIALS:
        return PlainTextResponse(
            "No hay credenciales en memoria. Vuelve a cargar el padrón.",
            status_code=404,
        )
    csv_text = participants_service.credentials_to_csv(_LAST_CREDENTIALS)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="credenciales.csv"'
        },
    )


# ===========================================================================
# API JSON (endpoints del spec)
# ===========================================================================
@router.post(
    "/upload_participants",
    response_model=UploadParticipantsOut,
    status_code=status.HTTP_201_CREATED,
    summary="Cargar el listado de participantes",
    dependencies=[Depends(require_admin)],
)
def upload_participants(
    payload: UploadParticipantsIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UploadParticipantsOut:
    """
    Reemplaza (o añade a) el padrón de participantes.

    Devuelve, **una única vez**, el PIN en claro y el link único de cada
    persona. Guárdalos: después solo se pueden regenerar.
    """
    global _LAST_CREDENTIALS

    try:
        credentials, n_exclusions = participants_service.replace_participants(
            db,
            payload.participants,
            payload.exclusions,
            settings,
            replace_existing=payload.replace_existing,
        )
        db.commit()
    except participants_service.ParticipantServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _LAST_CREDENTIALS = credentials
    return UploadParticipantsOut(
        created=len(credentials),
        exclusions=n_exclusions,
        credentials=credentials,
    )


@router.post(
    "/generate_assignments",
    response_model=GenerateAssignmentsOut,
    summary="Generar (o regenerar) el sorteo",
    dependencies=[Depends(require_admin)],
)
def generate_assignments_endpoint(
    payload: GenerateAssignmentsIn | None = None,
    db: Session = Depends(get_db),
) -> GenerateAssignmentsOut:
    """
    Ejecuta el algoritmo y persiste el resultado.

    Pasa `seed` para obtener un sorteo reproducible; si se omite, se genera
    una semilla aleatoria y se guarda junto al sorteo.
    """
    payload = payload or GenerateAssignmentsIn()
    try:
        draw, result = draws_service.create_draw(
            db, seed=payload.seed, notes=payload.notes
        )
        db.commit()
    except (draws_service.DrawServiceError, AssignmentError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    assignments = draws_service.get_assignments(db, draw)
    return GenerateAssignmentsOut(
        draw=DrawOut.model_validate(draw),
        assignments=draws_service.assignments_to_out(assignments),
        validation=draws_service.validate_draw(db, draw),
        attempts=result.attempts,
    )


@router.get(
    "/assignments",
    response_model=GenerateAssignmentsOut,
    summary="Ver todas las asignaciones del sorteo activo",
    dependencies=[Depends(require_admin)],
)
def list_assignments(db: Session = Depends(get_db)) -> GenerateAssignmentsOut:
    draw = draws_service.get_active_draw(db)
    if draw is None:
        raise HTTPException(status_code=404, detail="Todavía no hay ningún sorteo.")
    assignments = draws_service.get_assignments(db, draw)
    return GenerateAssignmentsOut(
        draw=DrawOut.model_validate(draw),
        assignments=draws_service.assignments_to_out(assignments),
        validation=draws_service.validate_draw(db, draw),
        attempts=0,
    )


@router.get(
    "/validate",
    response_model=ValidationReport,
    summary="Auditar el sorteo activo (regalos cruzados, exclusiones, etc.)",
    dependencies=[Depends(require_admin)],
)
def validate_endpoint(db: Session = Depends(get_db)) -> ValidationReport:
    draw = draws_service.get_active_draw(db)
    if draw is None:
        raise HTTPException(status_code=404, detail="Todavía no hay ningún sorteo.")
    return draws_service.validate_draw(db, draw)


@router.get(
    "/participants",
    response_model=list[ParticipantOut],
    summary="Listar participantes",
    dependencies=[Depends(require_admin)],
)
def list_participants_endpoint(db: Session = Depends(get_db)):
    return participants_service.list_participants(db)
