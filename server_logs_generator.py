#!/usr/bin/env python3
"""
Generador de logs de servidor: selecciona aleatoriamente una opción
de respuesta HTTP desde un pool y la imprime cada 100 ms.

Por ahora solo imprime la opción seleccionada. Se deja un hook para
procesamiento posterior.
"""

import json
import random
import sys
import time
from typing import Dict, Any

from response_pool import get_pool


def process_option(_option: Dict[str, Any]) -> None:
    """Hook para procesar la opción seleccionada en el futuro.

    TODO: implementar lógica de persistencia, envío a cola, etc.
    """
    # Por ahora no hace nada.
    return


def main() -> None:
    pool = [opt.to_dict() for opt in get_pool()]
    interval_seconds = 0.1  # 100 ms

    try:
        while True:
            option = random.choice(pool)
            print(json.dumps(option, ensure_ascii=False))
            sys.stdout.flush()

            # Hook para procesamiento adicional (por ahora no-op)
            process_option(option)

            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")


if __name__ == "__main__":
    main()
