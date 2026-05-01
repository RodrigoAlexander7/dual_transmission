# PENDIENTES A AJUSTAR: MUY IMPORTANTE
"""
- Evaluar si la camara permite capturas por hardware (sin pasar por el CPU) usando v4l2-ctl
correr: v4l2-ctl -d /dev/video4 --list-formats-ext
En caso sea soportado, usarlo 

- Propuesta de optimizacion:
1. Configurar la camara para capturar con v4l2-ctl en luegar de fswebcam
2. Usar ffmpeg para comprimir a jpeg con calidad 75 (o menor)
"""

# minimal example to test cam shot
import subprocess   # for running shell commands on python
import time         # control time 
import os           # create directories 

# Configuración de devices
TOTAL_FOTOS = 3
INTERVALO = 3

device = "/dev/video4"

def config_camera(device):
    """Configura la cámara para evitar barrido por movimiento"""
    try:
        # 1. Forzar exposición manual
        subprocess.run(
            ["v4l2-ctl", "-d", device, "-c", "auto_exposure=1"], check=True
        )
        # 2. Ajustar exposición 
        # Un valor menor = menos borroso, pero más oscuro.
        subprocess.run(
            ["v4l2-ctl", "-d", device, "-c", "exposure_time_absolute=300"],
            check=True,
        )
        print(f"{device} configurado con obturacion manual.")
    except Exception as e:
        print(f"No se pudo configurar {device}: {e}")


def capturar_individual(device, nombre_archivo):
    try:
        comando = ["fswebcam","-d",device,"-r","2560x720","--skip","20","--no-banner",nombre_archivo, "--jpeg","75"]
        subprocess.run(
            comando,
            check=True,  # , stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return True
    except Exception as e:
        print(f"Error en {device}: {e}")
        return False

def tomar_par_estereo(indice):
    timestamp = time.strftime("%H%M%S")
    ruta = f"fotos_{indice}_{timestamp}.jpg"

    # Creamos dos hilos para disparar las cámaras al "mismo" tiempo
    capturar_individual(device, ruta)

# --- Lógica Principal ---
if not os.path.exists("fotos_prueba"):
    os.makedirs("fotos_prueba")

config_camera(device)



for i in range(1, TOTAL_FOTOS + 1):
    tomar_par_estereo(i)
    if i < TOTAL_FOTOS:
        print(f"Esperando {INTERVALO} segundos...")
        time.sleep(INTERVALO)
