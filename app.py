import os
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from mercadopublico_scraper import MercadoPublicoScraper

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Flask ──────────────────────────────────────────────────────────────────────
app = Flask(__name__)

OUTPUT_DIR = "/tmp/licitaciones"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ── Health check ───────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "servicio": "MercadoPublico Scraper Chile"}), 200


# ── Endpoint principal ─────────────────────────────────────────────────────────
@app.route("/scrape", methods=["POST"])
def scrape():
    """
    Recibe fecha_inicio y fecha_fin, ejecuta el scraping de tarjetas
    y retorna las licitaciones como JSON.

    Body JSON:
        {
            "fecha_inicio": "2025-02-01",
            "fecha_fin":    "2025-02-28"
        }

    Response JSON:
        {
            "metadata": { ... },
            "licitaciones": [ { ... }, ... ]
        }
    """
    data = request.get_json(force=True, silent=True) or {}

    fecha_inicio_str = data.get("fecha_inicio", "")
    fecha_fin_str    = data.get("fecha_fin", "")

    # ── Validar fechas ─────────────────────────────────────────────────────────
    if not fecha_inicio_str or not fecha_fin_str:
        return jsonify({
            "error": "Se requieren 'fecha_inicio' y 'fecha_fin' en formato YYYY-MM-DD"
        }), 400

    try:
        fecha_inicio = datetime.strptime(fecha_inicio_str, "%Y-%m-%d")
        fecha_fin    = datetime.strptime(fecha_fin_str,    "%Y-%m-%d")
    except ValueError:
        return jsonify({
            "error": "Formato de fecha inválido. Usa YYYY-MM-DD (ej: 2025-02-01)"
        }), 400

    if fecha_fin < fecha_inicio:
        return jsonify({
            "error": "fecha_fin debe ser igual o posterior a fecha_inicio"
        }), 400

    logger.info(f"📥 /scrape — {fecha_inicio_str} → {fecha_fin_str}")

    # ── Ejecutar scraper ───────────────────────────────────────────────────────
    scraper = MercadoPublicoScraper(headless=True, output_dir=OUTPUT_DIR)
    try:
        scraper.iniciar()
        licitaciones = scraper.scrape(fecha_inicio, fecha_fin)
    except Exception as e:
        logger.exception("Error inesperado en el scraper")
        return jsonify({"error": str(e)}), 500
    finally:
        scraper.cerrar()

    if licitaciones is None:
        return jsonify({
            "error": "El scraper no pudo completar el proceso. Revisa los logs."
        }), 500

    # ── Guardar JSON en disco (respaldo) ───────────────────────────────────────
    try:
        ruta_json = scraper.guardar_json(licitaciones, fecha_inicio, fecha_fin)
        logger.info(f"💾 Respaldo guardado: {ruta_json}")
    except Exception as e:
        logger.warning(f"⚠️  No se pudo guardar respaldo JSON: {e}")

    # ── Respuesta ──────────────────────────────────────────────────────────────
    logger.info(f"✅ Retornando {len(licitaciones)} licitaciones")

    return jsonify({
        "metadata": {
            "fuente": "Mercado Público Chile",
            "fecha_inicio": fecha_inicio_str,
            "fecha_fin": fecha_fin_str,
            "total_licitaciones": len(licitaciones),
            "generado_en": datetime.now().isoformat(),
        },
        "licitaciones": licitaciones,
    }), 200


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Servidor en puerto {port}")
    app.run(host="0.0.0.0", port=port, debug=False)