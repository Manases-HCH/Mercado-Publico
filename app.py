import os
import logging
from datetime import datetime
from flask import Flask, request, jsonify, send_file
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
    y retorna el Excel como archivo adjunto.

    Body JSON:
        {
            "fecha_inicio": "2025-02-01",
            "fecha_fin":    "2025-02-28"
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
            "error": "Formato de fecha invalido. Usa YYYY-MM-DD (ej: 2025-02-01)"
        }), 400

    if fecha_fin < fecha_inicio:
        return jsonify({
            "error": "fecha_fin debe ser igual o posterior a fecha_inicio"
        }), 400

    logger.info(f"Recibiendo /scrape — {fecha_inicio_str} a {fecha_fin_str}")

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

    if not licitaciones:
        return jsonify({
            "error": "El scraper no encontro licitaciones para el rango indicado."
        }), 404

    # ── Generar Excel ──────────────────────────────────────────────────────────
    try:
        ruta_excel = scraper.guardar_excel(licitaciones, fecha_inicio, fecha_fin)
    except Exception as e:
        logger.exception("Error generando Excel")
        return jsonify({"error": f"Error generando Excel: {e}"}), 500

    nombre_descarga = f"LICIT_CHILE_{fecha_inicio.strftime('%y%m%d')}.xlsx"
    logger.info(f"Enviando Excel: {ruta_excel} ({len(licitaciones)} licitaciones)")

    return send_file(
        ruta_excel,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=nombre_descarga,
    )


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Servidor en puerto {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
