import sys
import json
import logging
import re
from datetime import datetime
from time import sleep
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from bs4 import BeautifulSoup

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

URL_BUSCADOR = "https://www.mercadopublico.cl/Home/BusquedaLicitacion"


class MercadoPublicoScraper:
    """
    Scraper Mercado Público Chile.
    Extrae todas las tarjetas de licitaciones con paginación automática.
    Salida: JSON.
    """

    def __init__(self, headless: bool = True, output_dir: str = None):
        self.headless = headless
        self.output_dir = Path(output_dir or Path.cwd()).resolve()
        self.driver = None
        logger.info(f"📁 Salida en: {self.output_dir}")

    # ── Navegador ──────────────────────────────────────────────────────────────
    def iniciar(self):
        logger.info("🚀 Iniciando Chrome...")
        options = Options()

        if self.headless:
            logger.info("   👻 Modo headless=new")
            options.add_argument("--headless=new")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=es-CL")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-infobars")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
        except Exception:
            self.driver = webdriver.Chrome(options=options)

        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        logger.info("✅ Chrome listo\n")

    def cerrar(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            logger.info("✅ Chrome cerrado")

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _wait(self, timeout: int = 30):
        return WebDriverWait(self.driver, timeout)

    def _js_click(self, element):
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        sleep(0.3)
        self.driver.execute_script("arguments[0].click();", element)

    def _js_set_date(self, element, value: str):
        self.driver.execute_script("arguments[0].removeAttribute('readonly');", element)
        self.driver.execute_script("arguments[0].value = '';", element)
        self.driver.execute_script("arguments[0].value = arguments[1];", element, value)
        for event in ["input", "change", "blur", "keyup"]:
            self.driver.execute_script(
                f"arguments[0].dispatchEvent(new Event('{event}', {{bubbles:true}}));", element
            )
        sleep(0.3)

    def _cerrar_popup(self):
        selectores = [
            "//button[@class='close']",
            "//button[contains(@data-dismiss,'modal')]",
            "//*[contains(@class,'modal') and contains(@style,'display: block')]//button[contains(@class,'close')]",
        ]
        for xpath in selectores:
            try:
                elem = self.driver.find_element(By.XPATH, xpath)
                if elem.is_displayed():
                    self._js_click(elem)
                    sleep(0.5)
                    logger.info("   ✓ Popup cerrado")
                    return
            except NoSuchElementException:
                continue

    def _entrar_iframe(self) -> bool:
        """Entra al iframe que contiene el buscador. Retorna True si lo encontró."""
        try:
            self._wait(30).until(
                lambda d: len(d.find_elements(By.TAG_NAME, "iframe")) > 0
            )
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            for i, frame in enumerate(iframes):
                try:
                    self.driver.switch_to.frame(frame)
                    self.driver.find_element(By.ID, "selectestado")
                    logger.info(f"   ✓ iframe[{i}] contiene el buscador")
                    return True
                except NoSuchElementException:
                    self.driver.switch_to.default_content()
            logger.error("❌ Ningún iframe contiene #selectestado")
            return False
        except TimeoutException:
            logger.error("❌ No se encontraron iframes")
            return False

    # ── Parser de tarjetas ─────────────────────────────────────────────────────
    def _parsear_tarjeta(self, card) -> dict:
        """
        Extrae todos los campos de una tarjeta .lic-bloq-wrap.
        
        Campos extraídos:
          - id_licitacion
          - tipo            (L1, LE, LP, LQ, LR, ...)
          - estado
          - titulo
          - descripcion
          - monto
          - fecha_publicacion
          - fecha_cierre
          - entidad
          - compras_efectuadas
          - reclamos_pago
          - url_ficha
        """
        data = {}

        # ── ID Licitación ──────────────────────────────────────────────────────
        id_div = card.find("div", class_="id-licitacion")
        if id_div:
            span = id_div.find("span")
            data["id_licitacion"] = span.get_text(strip=True) if span else ""
        else:
            data["id_licitacion"] = ""

        # ── Tipo y Estado ──────────────────────────────────────────────────────
        estado_div = card.find("div", class_="estado-lic")
        if estado_div:
            strongs = estado_div.find_all("strong")
            # primer <strong> → tipo (LR, LP, etc.)
            data["tipo"] = strongs[0].get_text(strip=True) if len(strongs) > 0 else ""
            # segundo <strong> → texto del estado
            data["estado"] = strongs[1].get_text(strip=True) if len(strongs) > 1 else ""
        else:
            data["tipo"] = ""
            data["estado"] = ""

        # ── Título ─────────────────────────────────────────────────────────────
        h2 = card.find("h2")
        data["titulo"] = h2.get_text(strip=True) if h2 else ""

        # ── URL de ficha (extraída del onclick del enlace del h2) ──────────────
        if h2:
            a_tag = h2.find_parent("a") or card.find("a", onclick=True)
        else:
            a_tag = card.find("a", onclick=True)

        data["url_ficha"] = ""
        if a_tag:
            onclick_val = a_tag.get("onclick", "")
            match = re.search(r"verFicha\('([^']+)'\)", onclick_val)
            if match:
                data["url_ficha"] = match.group(1)

        # ── Descripción (primer <p> del body, después del h2) ─────────────────
        body = card.find("div", class_="lic-block-body")
        descripcion = ""
        if body:
            parrafos = body.find_all("p", class_="text-weight-light")
            if parrafos:
                descripcion = parrafos[0].get_text(strip=True)
        data["descripcion"] = descripcion

        # ── Monto, Fecha publicación, Fecha cierre ─────────────────────────────
        # Están en divs con clases específicas dentro de .margin-bottom-md.row
        data["monto"] = ""
        data["fecha_publicacion"] = ""
        data["fecha_cierre"] = ""

        if body:
            # Monto
            monto_div = body.find("div", class_="monto-dis")
            if monto_div:
                span_monto = monto_div.find("span")
                data["monto"] = span_monto.get_text(strip=True) if span_monto else ""

            # Fechas: todos los col-md-4 dentro del row de fechas
            fecha_row = body.find("div", class_="margin-bottom-md row")
            if fecha_row:
                col_divs = fecha_row.find_all("div", class_="col-md-4")
                for col in col_divs:
                    label = col.find("p")
                    valor = col.find("span", class_="highlight-text")
                    if not label or not valor:
                        continue
                    label_text = label.get_text(strip=True).lower()
                    valor_text = valor.get_text(strip=True)
                    if "publicaci" in label_text:
                        data["fecha_publicacion"] = valor_text
                    elif "cierre" in label_text:
                        data["fecha_cierre"] = valor_text

        # ── Footer: entidad, compras, reclamos ────────────────────────────────
        footer = card.find("div", class_="lic-bloq-footer")
        data["entidad"] = ""
        data["compras_efectuadas"] = ""
        data["reclamos_pago"] = ""

        if footer:
            col_divs = footer.find_all("div", class_=re.compile(r"col-md-4"))
            for i, col in enumerate(col_divs):
                if i == 0:
                    # Entidad: primer <strong>
                    strong_entidad = col.find("strong")
                    data["entidad"] = strong_entidad.get_text(strip=True) if strong_entidad else ""
                elif i == 1:
                    span_val = col.find("span", class_="highlight-text")
                    data["compras_efectuadas"] = span_val.get_text(strip=True) if span_val else ""
                elif i == 2:
                    span_val = col.find("span", class_="highlight-text")
                    data["reclamos_pago"] = span_val.get_text(strip=True) if span_val else ""

        return data

    def _scrapear_pagina_actual(self) -> list:
        """Parsea todas las tarjetas de la página actual dentro del iframe."""
        sleep(2)  # Esperar que carguen los resultados
        html = self.driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_="lic-bloq-wrap")
        logger.info(f"   📦 Tarjetas encontradas en esta página: {len(cards)}")
        return [self._parsear_tarjeta(c) for c in cards]

    def _obtener_info_paginacion(self) -> tuple[int, int]:
        """
        Lee el paginador y devuelve (pagina_actual, ultima_pagina).
        El paginador tiene:
          - <li class="current">N</li>  → página actual
          - <a class="next-pager" onclick="$.Busqueda.buscar(N)">  → siguiente
          - Los <a onclick="$.Busqueda.buscar(N)"> → páginas disponibles
        """
        html = self.driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        paginador = soup.find("div", class_="paginador")

        if not paginador:
            return 1, 1

        # Página actual
        current_li = paginador.find("li", class_="current")
        try:
            pagina_actual = int(current_li.get_text(strip=True))
        except (AttributeError, ValueError):
            pagina_actual = 1

        # Última página: máximo número entre todos los onclick="$.Busqueda.buscar(N)"
        numeros = []
        for a in paginador.find_all("a", onclick=True):
            match = re.search(r"buscar\((\d+)\)", a["onclick"])
            if match:
                numeros.append(int(match.group(1)))

        ultima_pagina = max(numeros) if numeros else pagina_actual

        return pagina_actual, ultima_pagina

    def _ir_siguiente_pagina(self, pagina_actual: int) -> bool:
        """
        Ejecuta $.Busqueda.buscar(pagina_actual + 1) via JS en el iframe.
        Retorna True si avanzó, False si ya era la última página.
        """
        pagina_sig = pagina_actual + 1

        # Verificar que existe el enlace a la siguiente página en el DOM
        try:
            # Buscar el <a class="next-pager"> — indica que hay página siguiente
            next_btn = self.driver.find_element(By.CSS_SELECTOR, "a.next-pager")
            if not next_btn.is_displayed():
                logger.info("   🏁 No hay más páginas (next-pager oculto)")
                return False
        except NoSuchElementException:
            logger.info("   🏁 No hay más páginas (next-pager ausente)")
            return False

        logger.info(f"   ➡️  Yendo a página {pagina_sig} via $.Busqueda.buscar({pagina_sig})...")
        self.driver.execute_script(f"$.Busqueda.buscar({pagina_sig});")
        sleep(3)
        return True

    # ── Flujo principal ────────────────────────────────────────────────────────
    def scrape(self, fecha_inicio: datetime, fecha_fin: datetime) -> list:
        fi_str = fecha_inicio.strftime("%d/%m/%Y")
        ff_str = fecha_fin.strftime("%d/%m/%Y")
        logger.info(f"📅 Rango: {fi_str} → {ff_str}")

        # ── 1. Cargar página ───────────────────────────────────────────────────
        logger.info(f"🌐 Cargando {URL_BUSCADOR} ...")
        self.driver.get(URL_BUSCADOR)
        sleep(5)
        self._cerrar_popup()

        # ── 2. Entrar al iframe ────────────────────────────────────────────────
        logger.info("🔖 Buscando iframe del buscador...")
        if not self._entrar_iframe():
            logger.error("❌ No se pudo entrar al iframe")
            return []

        # ── 3. Estado → Todos los estados ─────────────────────────────────────
        logger.info("🔘 Seleccionando estado: Todos los estados...")
        try:
            select_elem = self._wait(15).until(
                EC.presence_of_element_located((By.ID, "selectestado"))
            )
            Select(select_elem).select_by_value("-1")
            logger.info("   ✓ Estado = Todos")
            sleep(1)
        except TimeoutException:
            logger.error("❌ No se encontró #selectestado")
            return []

        # ── 4. Fecha DESDE ─────────────────────────────────────────────────────
        logger.info(f"📝 Fecha desde: {fi_str}")
        try:
            campo_desde = self._wait(15).until(
                EC.presence_of_element_located((By.ID, "fechadesde"))
            )
            self._js_set_date(campo_desde, fi_str)
        except TimeoutException:
            logger.error("❌ No se encontró #fechadesde")
            return []

        # ── 5. Fecha HASTA ─────────────────────────────────────────────────────
        logger.info(f"📝 Fecha hasta: {ff_str}")
        try:
            campo_hasta = self._wait(15).until(
                EC.presence_of_element_located((By.ID, "fechahasta"))
            )
            self._js_set_date(campo_hasta, ff_str)
        except TimeoutException:
            logger.error("❌ No se encontró #fechahasta")
            return []

        # ── 6. Botón Buscar ────────────────────────────────────────────────────
        logger.info("🔍 Ejecutando búsqueda...")
        candidatos_buscar = [
            (By.ID,    "btnBuscarLicitacion"),
            (By.XPATH, "//button[contains(.,'Buscar')]"),
            (By.XPATH, "//a[contains(.,'Buscar')]"),
            (By.XPATH, "//*[contains(@onclick,'Busqueda.buscar')]"),
        ]
        for by, selector in candidatos_buscar:
            try:
                btn = self.driver.find_element(by, selector)
                if btn.is_displayed():
                    self._js_click(btn)
                    logger.info(f"   ✓ Clic en Buscar")
                    break
            except NoSuchElementException:
                continue

        # ── 7. Esperar primeros resultados ─────────────────────────────────────
        logger.info("⏳ Esperando resultados (hasta 60 s)...")
        try:
            self._wait(60).until(
                EC.presence_of_element_located((By.CLASS_NAME, "lic-bloq-wrap"))
            )
            logger.info("   ✓ Primeros resultados visibles")
        except TimeoutException:
            logger.error("❌ No aparecieron tarjetas .lic-bloq-wrap en 60 s")
            return []

        # ── 8. Paginación: recorrer todas las páginas ──────────────────────────
        todas_licitaciones = []

        while True:
            # Leer estado actual del paginador
            pagina_actual, ultima_pagina = self._obtener_info_paginacion()
            logger.info(f"\n📄 Scrapeando página {pagina_actual} de {ultima_pagina}...")

            licitaciones_pagina = self._scrapear_pagina_actual()
            todas_licitaciones.extend(licitaciones_pagina)
            logger.info(f"   ✅ Total acumulado: {len(todas_licitaciones)} licitaciones")

            # Salir si ya estamos en la última página
            if pagina_actual >= ultima_pagina:
                logger.info("   🏁 Última página alcanzada")
                break

            if not self._ir_siguiente_pagina(pagina_actual):
                break

            # Esperar que cargue la nueva página
            try:
                self._wait(30).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "lic-bloq-wrap"))
                )
            except TimeoutException:
                logger.warning("⚠️  Timeout esperando tarjetas en nueva página — deteniendo")
                break

        logger.info(f"\n🎯 Scraping finalizado. Total: {len(todas_licitaciones)} licitaciones")
        return todas_licitaciones

    # ── Guardar JSON ───────────────────────────────────────────────────────────
    def guardar_json(self, datos: list, fecha_inicio: datetime, fecha_fin: datetime) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fi = fecha_inicio.strftime("%Y%m%d")
        ff = fecha_fin.strftime("%Y%m%d")
        nombre = f"licitaciones_chile_{fi}_{ff}_{timestamp}.json"
        ruta = self.output_dir / nombre

        output = {
            "metadata": {
                "fuente": "Mercado Público Chile",
                "url": URL_BUSCADOR,
                "fecha_inicio": fecha_inicio.strftime("%d/%m/%Y"),
                "fecha_fin": fecha_fin.strftime("%d/%m/%Y"),
                "total_licitaciones": len(datos),
                "generado_en": datetime.now().isoformat(),
            },
            "licitaciones": datos,
        }

        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 JSON guardado: {ruta}")
        return str(ruta)


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    print("\n" + "=" * 65)
    print("🇨🇱  MERCADO PÚBLICO CHILE — Scraper de Licitaciones")
    print("=" * 65)

    headless = "--headless" in sys.argv
    if headless:
        sys.argv.remove("--headless")

    if len(sys.argv) >= 3:
        try:
            fecha_inicio = datetime.strptime(sys.argv[1], "%Y-%m-%d")
            fecha_fin    = datetime.strptime(sys.argv[2], "%Y-%m-%d")
        except ValueError:
            print("Uso: python mercadopublico_scraper.py YYYY-MM-DD YYYY-MM-DD [--headless]")
            return
    else:
        print("\n📅 Ingresa las fechas (formato: DD/MM/YYYY)\n")
        while True:
            try:
                fecha_inicio = datetime.strptime(input("Fecha inicio: ").strip(), "%d/%m/%Y")
                break
            except ValueError:
                print("❌ Usa DD/MM/YYYY")
        while True:
            try:
                fecha_fin = datetime.strptime(input("Fecha fin:    ").strip(), "%d/%m/%Y")
                break
            except ValueError:
                print("❌ Usa DD/MM/YYYY")

    if fecha_fin < fecha_inicio:
        print("❌ La fecha fin debe ser posterior a la fecha inicio")
        return

    scraper = MercadoPublicoScraper(headless=headless)
    try:
        scraper.iniciar()
        datos = scraper.scrape(fecha_inicio, fecha_fin)
        if datos:
            ruta = scraper.guardar_json(datos, fecha_inicio, fecha_fin)
            print(f"\n✅ Listo — {len(datos)} licitaciones guardadas en:\n   {ruta}")
        else:
            print("\n⚠️  No se obtuvieron licitaciones.")
    except KeyboardInterrupt:
        print("\n⚠️  Interrumpido por el usuario")
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        scraper.cerrar()


if __name__ == "__main__":
    main()