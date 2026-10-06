# Datos experimentales

## Series medidas

| Archivo | Contenido | Wiki |
|---|---|---|
| `ruido-reposo-hcsr04.csv` | 10 disparos con superficie en reposo | §8.2 |
| `repetibilidad-operacion.csv` | 8 muestras consecutivas en operación | §8.3, §8.4, §8.5 |
| `ldr-canales-adc.csv` | Verificación del LDR sobre el ADC1 | §8.1 |

## Series pendientes

| Archivo | Contenido | Wiki |
|---|---|---|
| `calibracion-nivel.csv` | Pares regla / sensor, subiendo y bajando | §8.6 |
| `linea-base.csv` | Serie nocturna para percentiles Q80 y Q90 | §5.4 |
| `aforos.csv` | Tres aforos volumétricos para la curva de gasto | §6 |
| `casos-fusion.csv` | Veinte casos etiquetados y resultado de la alarma | §8.7 |

El histórico del dispositivo se exporta directamente desde `http://IP/api/historico`.

## Formato sugerido para las pendientes

**`calibracion-nivel.csv`**
```
punto,regla_cm,sensor_subiendo_cm,sensor_bajando_cm,temp_C
1,2.0,,,
2,4.0,,,
```

**`casos-fusion.csv`**
```
caso,tipo,etiqueta_verdadera,alarma_activada,resultado
1,descenso_sostenido,evento,,
2,pico_aislado,no_evento,,
```
