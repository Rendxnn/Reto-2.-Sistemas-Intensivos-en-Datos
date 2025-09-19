from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any


@dataclass(frozen=True)
class ResponseOption:
    method: str
    path: str
    status_code: int
    error_code: Optional[str]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Pool de opciones de respuesta HTTP simuladas
# - status_code varía entre respuestas exitosas, redirecciones, cliente y servidor
# - error_code es None cuando no hay error; de lo contrario, un código de error de app
POOL: List[ResponseOption] = [
    # Éxitos
    ResponseOption("GET", "/api/health", 200, None, "OK"),
    ResponseOption("GET", "/api/users", 200, None, "OK"),
    ResponseOption("POST", "/api/users", 201, None, "Created"),
    ResponseOption("PUT", "/api/users/42", 200, None, "Updated"),
    ResponseOption("DELETE", "/api/users/42", 204, None, "No Content"),
    ResponseOption("GET", "/static/logo.png", 304, None, "Not Modified"),

    # Redirecciones
    ResponseOption("GET", "/", 301, None, "Moved Permanently"),
    ResponseOption("GET", "/old-endpoint", 302, None, "Found"),

    # Errores del cliente
    ResponseOption("GET", "/api/secret", 401, "EAUTH", "Unauthorized"),
    ResponseOption("GET", "/api/secret", 403, "EFORBIDDEN", "Forbidden"),
    ResponseOption("GET", "/api/unknown", 404, "ENOTFOUND", "Not Found"),
    ResponseOption("POST", "/api/users", 409, "ECONFLICT", "Conflict"),
    ResponseOption("GET", "/api/slow", 408, "ETIMEOUT", "Request Timeout"),
    ResponseOption("GET", "/api/limited", 429, "ERATE", "Too Many Requests"),

    # Errores del servidor
    ResponseOption("GET", "/api/report", 500, "ESERVER", "Internal Server Error"),
    ResponseOption("GET", "/api/proxy", 502, "EBADGATEWAY", "Bad Gateway"),
    ResponseOption("GET", "/api/external", 503, "EUNAVAILABLE", "Service Unavailable"),
    ResponseOption("GET", "/api/external", 504, "EGATEWAYTIMEOUT", "Gateway Timeout"),
]


def get_pool() -> List[ResponseOption]:
    """Devuelve el pool de opciones de respuesta disponible."""
    return POOL

