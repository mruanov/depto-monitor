# Monitor de departamentos en Android TV Box (Termux)

Portal Inmobiliario bloquea las IPs de datacenter de GitHub Actions y redirige a
`/gz/account-verification` (pantalla de login de Mercado Libre). Por eso el
monitoreo real se ejecuta desde una red residencial usando Termux en el Android
TV Box. El workflow de GitHub queda solo como informativo.

## Requisitos

- Android 7 o superior (TV Box encendido 24/7).
- Termux instalado desde **F-Droid** (no desde Play Store):
  <https://f-droid.org/packages/com.termux/>
- Opcional pero recomendado: **Termux:Boot** desde F-Droid, para relanzar el
  cron después de un reinicio.
- Cuenta de Telegram con un bot creado en **@BotFather** y el chat ID obtenido
  con **@userinfobot**.

### Instalar Termux en el TV Box

Si el TV Box no tiene Play Store o navegador cómodo, descarga el APK de F-Droid
en otro equipo y instálalo por ADB:

```bash
adb connect <IP_DEL_TV_BOX>:5555
adb install Termux.apk
adb install TermuxBoot.apk
```

También puedes copiar los APK a un pendrive y abrirlos con un explorador de
archivos del TV Box.

## Instalación rápida

Abre Termux y ejecuta:

```bash
pkg update && pkg upgrade -y
pkg install -y git
git clone https://github.com/mruanov/depto-monitor.git
cd depto-monitor
bash setup_termux.sh
```

El script:

1. Instala Python, `cronie`, `termux-services`, `tzdata` y certificados.
2. Crea el entorno virtual e instala `requirements-termux.txt`.
3. Pide el token y chat ID de Telegram y los guarda en `.env` (permisos `600`).
4. Verifica la conexión con Telegram.
5. Programa el ciclo cada 4 horas en `crontab`.
6. Habilita `crond`, el wake-lock y el arranque con Termux:Boot.

## Verificación manual

```bash
cd ~/depto-monitor

# Solo verifica Telegram
./run_monitor.sh --verify

# Ciclo de prueba: no guarda en la base ni envía mensajes
./run_monitor.sh --test

# Ciclo real
./run_monitor.sh
```

El log del cron queda en `~/depto-monitor/monitor-cron.log`:

```bash
tail -f ~/depto-monitor/monitor-cron.log
```

## Configuración del cron

El script deja una línea similar a esta:

```cron
0 */4 * * * /data/data/com.termux/files/home/depto-monitor/run_monitor.sh >> /data/data/com.termux/files/home/depto-monitor/monitor-cron.log 2>&1
```

Para editarla manualmente:

```bash
crontab -e
```

Para comprobar que `crond` está activo:

```bash
sv status crond
```

Si no está corriendo:

```bash
sv-enable crond
sv up crond
```

## Importante en Android

1. **Desactiva la optimización de batería para Termux** en
   `Ajustes > Aplicaciones > Termux > Batería > Sin restricciones`.
2. Mantén el wake-lock activo: `termux-wake-lock`.
3. Instala **Termux:Boot** y abre la app una vez para que registre el arranque.
4. Si el TV Box se reinicia y no usas Termux:Boot, abre Termux y ejecuta:

   ```bash
   termux-wake-lock
   sv up crond
   ```

## Dependencias en Termux

`requirements-termux.txt` instala solo:

- `requests`
- `beautifulsoup4`

No se instala Playwright ni `curl_cffi` en Termux: desde una IP residencial no
son necesarios. Si `lxml` está disponible se usa automáticamente; si no,
BeautifulSoup cae a `html.parser`.

## Actualizar el proyecto

```bash
cd ~/depto-monitor
git pull --ff-only
./.venv/bin/pip install -r requirements-termux.txt
./run_monitor.sh --test
```

## Problemas frecuentes

- **`Permission denied` al ejecutar `run_monitor.sh`**:
  `chmod +x run_monitor.sh setup_termux.sh`.
- **No llegan mensajes**: ejecuta `./run_monitor.sh --verify` y revisa que el
  token y el chat ID en `.env` sean correctos.
- **El cron no se ejecuta**: confirma `sv status crond`, que Termux tenga la
  batería sin restricciones y que el TV Box esté encendido.
- **El portal pide verificación de cuenta**: el TV Box debe estar en una red
  residencial chilena. Si ocurre igualmente, espera unos minutos y vuelve a
  probar con `./run_monitor.sh --test`.
- **La UF usa respaldo estático**: es normal si `mindicador.cl` no responde;
  el monitor usa la última UF cacheada o `UF_FALLBACK`.

## GitHub Actions

El workflow `.github/workflows/agente_deptos.yml` quedó deshabilitado y solo
sirve como referencia/informativo. No ejecuta scraping porque las IPs de
GitHub son redirigidas a la verificación de cuenta del portal.
