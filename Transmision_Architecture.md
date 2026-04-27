Para transmitir tanto imagenes como datos de sensores en una raspberry pi zero 2w mediante un xbee XR900 donde la eficiencia e integridad de datos son importantes
El receptor sera una computadora(laptop moderna) con otro xbee XR900 (conectado mediante Xbee adapter)
El xbee (emisor) y la laptop (receptor), estan a una distancia de 500m, con linea de vista visible en un entorno urbano

Se enviaran datos de varios sensores los cuales seran los siguientes
            "time": now_ms,
            "alt_ms5611": 0.0,
            "alt_bme280": 0.0,
            "pressure": 0.0,
            "temperature": 0.0,
            "velocity_z": 0.0,
            "accel_x": 0.0,
            "accel_y": 0.0,
            "accel_z": 0.0,
            "gyro_z": 0.0,
            "voltage": 0.0,
            "current": 0.0,
            "packets_received": 0,

1- En que formato y tipo de archivo se deberian escribir los sensores en la rapsberry para reducir su impacto de escritura?

2- como deberia serl el payload de los sensores para minimizar su tamanio? se podrian enviar en un solo paquete de telemetria (considerar las restricciones del xbee)

3- para leer los datos, deberia usar multiprocessing, thereading u otro metodo?, debemos considerar que multiprocessing es mas pesado pero puede evitar bloqueos
    # Preeliminarmente se usara threading para pruebas

4- Para transmitir datos lo pienzo hacer mediante serial, en modo api, para no complicarme creando el paquete desde cero

5- cada cuanto tipo debo leer datos de los sensores y enviarlos? 
el tiempo debe ser fijo? 
    Yo pensaba en leer sensore constantemente (a 5 Hz) y tener una variable current. Enviar los datos en un ratio 10:1 (10 paquetes de chuncks de imagenes, uno para telemetria) luego, cada vez que sea turno de enviar 01 paquete de telemetria, enviar el valor de current

6- Si uso el modo api, puedo crear el payload como yo desee? entonces como controlaria desde el receptor que:
    - el paquete llego integro y no debe descartarse
    - como saber que paquete es de telemetria y cual de imagen 

7- Como segmentar y enviar la imagen

8- Como enviar los paquetes por el xbee, en flujo continuo (sin pausas ni descanzos), creo que eso podria ser peligroso

9- Usar CTS/RTS en modo api, asi no tenemos que configurar esperas ni cuando enviar o recivir manualmente

10- Para esta implementacion sera mejor usar el modo API

------------------------------------------------------------------------------------



