# Estrategia "Agotamientos" — Cristian (TFT)
### Pseudocódigo fiel al video "Clase 2 - Estructura de Mercado / Agotamientos"

> Objetivo: replicar EXACTAMENTE lo que hace Cristian en el video, sin optimizaciones
> ni parámetros inventados. Cada regla cita de dónde sale en la clase.
> Fuente: transcripts/Clase 2. ESTRUCTURA DE MERCADO AGOTAMIENTOS.txt
>
> NOTA: El video explica el caso ALCISTA (compras). El caso BAJISTA y el cambio de
> tendencia Cristian dijo que los explica "mañana" (clase que NO tenemos). El bloque
> bajista de abajo es el ESPEJO exacto del alcista; confirmar con esa clase si aparece.

---

## 1. PARÁMETROS (tal como en el video)

```
MERCADO            = MNQ            # Micro E-mini Nasdaq-100 (futuros). Cristian: futuros regulados.
TF_NIVELES         = [60m, 30m, 15m]   # solo para trazar Soportes/Resistencias en premercado
TF_ESTRUCTURA      = 5m             # módulo de arranque + estructura + confirmación
TF_ENTRADA         = 1m            # ejecución (Cristian permite 1m o 2m; usa 1m)

# Sesiones (horario del video = Colombia/ET, que en dic-2023 coinciden, UTC-5)
APERTURA_ASIATICA  = 17:00         # inicio de la ventana de análisis
APERTURA_EUROPEA   = 02:00
APERTURA_NY        = 09:30         # evento clave: alta volatilidad, "se unen todas las bolsas"
# Regla del video: NO se tiene en cuenta la sesión americana del DÍA ANTERIOR.
# El módulo de arranque se busca DESDE la apertura asiática del día actual.

TP_RATIO           = 1.0           # Cristian usó "1 a 1" en el ejemplo (risk:reward)
# SL: en el "último rebote" (swing previo). No es un valor fijo: se mide al swing real.
# TAMAÑO: "lo normal para sus cuentas" (Cristian no da un número -> lo define la cuenta de fondeo).
```

---

## 2. DEFINICIONES (conceptos del video traducidos a reglas)

```
# --- Swing alto / bajo (pivote) ---
# Cristian lee "máximos y mínimos" de la estructura a ojo. Equivalente operativo:
SwingLow(i)  = vela cuyo mínimo es menor que el de las velas vecinas (mínimo local)
SwingHigh(i) = vela cuyo máximo es mayor que el de las velas vecinas (máximo local)

# --- Módulo de arranque (en 5m) ---
# "El precio más bajo de un mercado alcista desde donde se impulsa el mercado."
# Se busca hacia atrás, DESDE la apertura asiática, el mínimo desde el que arranca el impulso.
ModuloArranqueAlcista = el SwingLow más bajo desde APERTURA_ASIATICA hasta ahora (en 5m)
ModuloArranqueBajista = el SwingHigh más alto desde APERTURA_ASIATICA hasta ahora (en 5m)

# --- Estructura de mercado ---
# Alcista: la secuencia respeta mínimos crecientes (cada retroceso NO rompe el mínimo previo).
# "Respeta estructura" = el último impulso supera el máximo previo y el retroceso respeta
#   el mínimo previo (módulo de arranque).
# Ondas: ABC -> fallo de quinta (5) -> fallo de séptima (7). (contexto, no gatillo)

# --- Línea de tendencia (referencia, no entrada) ---
# Alcista: se traza uniendo el módulo de arranque con el PRIMER mínimo posterior
#          (se marca SIEMPRE por la parte de ABAJO).
# Bajista: se traza por la parte de ARRIBA (uniendo máximos).

# --- Línea de agotamiento (el gatillo) ---
# Es el retroceso de "velas continuas" contra el impulso.
# Alcista: retroceso = velas ROJAS consecutivas. La línea se traza por la parte de ARRIBA
#          de esas velas, MECHA INCLUIDA. Conforme imprimen velas rojas más bajas,
#          la línea se va ajustando al máximo (con mecha) de la última vela roja.
# Bajista: retroceso = velas VERDES consecutivas; línea por la parte de ABAJO, mecha incluida.
LineaAgotamientoAlcista = máximo (incluyendo mecha) de la última vela roja del retroceso
LineaAgotamientoBajista = mínimo (incluyendo mecha) de la última vela verde del retroceso
```

---

## 3. PREMERCADO (una vez, antes de operar)

```
funcion Premercado():
    # Cristian: "sacar soportes y resistencias de temporalidad alta hacia atrás"
    SR = []
    para cada TF en TF_NIVELES (60m -> 30m -> 15m):
        identificar niveles donde el precio rebotó/fue rechazado (>= 3 toques = nivel fuerte)
        corroborar el mismo nivel bajando de temporalidad
    SR = lista final de Soportes y Resistencias (precios)
    retornar SR
```

---

## 4. LÓGICA PRINCIPAL (caso ALCISTA — el del video)

```
SR = Premercado()
ModuloArranque = ModuloArranqueAlcista (en 5m, desde apertura asiática)

# ---- PASO 1: IMPULSO que rompe estructura ----
# "ese rompimiento / ese impulso es el paso número uno". NO se entra aquí.
esperar hasta que:
    el precio haga un IMPULSO alcista que ROMPA la estructura previa
    (supere el último SwingHigh relevante) -> se crea un NUEVO módulo de arranque alcista
Impulso = ese movimiento
PrecioImpulso = mínimo desde donde arrancó el impulso (nuevo módulo de arranque)

# ---- PASO 2: RETROCESO (agotamiento) ----
# Esperar un retroceso de velas ROJAS continuas que RESPETE el módulo de arranque.
esperar a que se forme un retroceso de velas rojas consecutivas
mientras (retroceso en curso):
    si (mínimo del retroceso < PrecioImpulso):   # rompió el módulo de arranque
        CANCELAR setup  (ya no respeta estructura)
    LineaAgotamiento = máximo (con mecha) de la última vela roja   # se va ajustando

# ---- PASO 3: ENTRADA (gatillo) ----
# "cuando me salga una vela que rompa esta línea de agotamiento, coloco la posición."
# Ejecución en 1m; estructura en 5m debe estar a favor (alcista).
cuando (una vela en TF_ENTRADA cierra/rompe POR ENCIMA de LineaAgotamiento):
    si (Confirmacion5m == alcista) Y (FiltroSR_OK para compra):
        ENTRAR EN COMPRA al precio de ruptura
        colocar Stop y Profit (sección 6)
```

### Confirmación de 5m
```
funcion Confirmacion5m_alcista():
    # "que en 5 minutos también esté a favor la vela / la estructura sea alcista"
    retornar (estructura en 5m es alcista: máximos y mínimos crecientes,
              respetando el módulo de arranque)
```

---

## 5. LÓGICA PRINCIPAL (caso BAJISTA — ESPEJO, confirmar con la clase faltante)

```
ModuloArranque = ModuloArranqueBajista (5m, desde apertura asiática)

# PASO 1: impulso BAJISTA que rompe estructura (nuevo módulo de arranque bajista). No se entra.
# PASO 2: retroceso de velas VERDES continuas que respete el módulo de arranque (máximo).
#         si (máximo del retroceso > PrecioImpulso) -> CANCELAR.
#         LineaAgotamiento = mínimo (con mecha) de la última vela verde.
# PASO 3: cuando una vela en 1m rompe POR DEBAJO de LineaAgotamiento
#         y estructura 5m es bajista y FiltroSR_OK para venta -> ENTRAR EN VENTA.
```

---

## 6. GESTIÓN DE LA OPERACIÓN (stop / profit)

```
funcion ColocarStopYProfit(direccion, precio_entrada):
    # ---- STOP-LOSS ----
    # Cristian: "el stop en el último rebote" (no muy corto, que deje respirar).
    si direccion == COMPRA:
        SL = mínimo del ÚLTIMO REBOTE (último SwingLow antes de la entrada)
             # alternativa válida del video: justo por debajo del soporte/resistencia relevante
    si direccion == VENTA:
        SL = máximo del último rebote (último SwingHigh antes de la entrada)

    riesgo = |precio_entrada - SL|

    # ---- TAKE-PROFIT ----
    # Cristian usó "1 a 1" (risk:reward).
    si direccion == COMPRA: TP = precio_entrada + TP_RATIO * riesgo
    si direccion == VENTA:  TP = precio_entrada - TP_RATIO * riesgo

    # ---- REGLA DE ORO ----
    # Una vez colocada la posición con su SL, NO se cierra manual.
    # "ya está tomada la decisión, dejar que el mercado haga lo suyo."
```

---

## 7. FILTRO DE SOPORTES / RESISTENCIAS (regla "ley" del video)

```
funcion FiltroSR_OK(direccion, precio_entrada):
    # "NO se puede entrar cerca a una resistencia de alta temporalidad. Eso es ley."
    si direccion == COMPRA:
        si hay una RESISTENCIA de TF alto justo por ENCIMA y cerca del precio_entrada:
            retornar FALSO   # no entrar
        # Cristian: si el precio ROMPE esa resistencia, la retestea (techo -> piso),
        # y entonces SÍ se puede entrar usando ese nivel como soporte.
    si direccion == VENTA:
        si hay un SOPORTE de TF alto justo por DEBAJO y cerca del precio_entrada:
            retornar FALSO
    retornar VERDADERO
```

---

## 8. RESUMEN DEL FLUJO (una sola pasada)

```
1. Premercado: trazar S/R en 60/30/15m.
2. En 5m: ubicar el módulo de arranque desde la apertura asiática.
3. Esperar el IMPULSO que rompe estructura  (Paso 1, no entrar).
4. Esperar el RETROCESO de velas continuas que respeta el módulo (Paso 2).
5. Trazar la línea de agotamiento (con mecha) sobre el retroceso.
6. ENTRAR cuando una vela (1m) rompe la línea de agotamiento,
   con estructura 5m a favor y sin S/R de alta temporalidad pegada.
7. SL en el último rebote; TP a 1:1.
8. No cerrar manual: dejar correr hasta SL o TP.
```

---

## 9. PUNTOS QUE EL VIDEO DEJA "A OJO" (para construir en AlgoWizard)

Estos NO son cambios a la estrategia: son los lugares donde Cristian decide visualmente
y, para automatizar en AlgoWizard, hay que elegir la traducción más fiel:

- **Pivote (swing):** cuántas velas a cada lado definen un máximo/mínimo (fiel: el swing que el ojo ve = típicamente 2–3 velas a cada lado).
- **"Velas continuas" del retroceso:** mínimo de velas rojas/verdes seguidas (en el video el retroceso es claramente una secuencia; fiel: >= 2 velas).
- **"Cerca" de un S/R:** a qué distancia se considera "pegado" (fiel: dentro del rango de ruido del nivel).
- **Buffer del stop:** ticks por debajo/encima del rebote (fiel: el mínimo/máximo exacto del rebote).

> Todo lo demás está tomado literal del video.
```

---
---

# v2 — Plan detallado (apuntes de Juli + mentorías de Cristian Marín)

> Esta sección REFINA y AMPLÍA el pseudocódigo de arriba con el plan operativo real
> que sigue Cristian, según los apuntes de Juli (clases 23–30 sep). Donde haya conflicto,
> manda esta v2.

## A. Plataforma y contexto real
- Cris opera en **NinjaTrader** con **futuros reales MNQ** (micro Nasdaq).
- Cuentas de fondeo que usa: **APEX** y **MY FUNDED FUTURES** (prop firms de FUTUROS).
- Usa **ATM Soul** (herramienta de trailing/gestión de NinjaTrader) para escalar salidas.
- Horarios en **hora de Denver (Mountain Time)**. (ET = MT + 2h.)
- OJO: WS Funded es CFD/MT5, NO futuros. La lógica aplica igual, pero el ecosistema
  natural de esta estrategia es NinjaTrader + Apex/My Funded Futures.

## B. Premercado (30 min antes — "primordial")
- Marcar **máximo y mínimo del día anterior** → soporte/resistencia principales.
- Bajar a **15m y 5m** para marcar otros S/R.
- Revisar comportamiento de **Londres y Asia** en 15m y 5m para detectar posible
  manipulación institucional (NO siempre la hay).
- Especular dirección en gráficos de **5 / 10 / 15 min**.
- Identificar **módulo de arranque + zonas de riesgo ANTES de la apertura de NY** (5 y 15 min).

## C. Plan de entrada (refinado)
1. Identificar **módulo de arranque** en sesión de **LONDRES o NUEVA YORK**.
2. Trazar **línea de tendencia en 5 min**.
3. Identificar **retroceso en 5 min** de 1+ velas (ideal que llegue a una resistencia previa).
4. Una vez clara la dirección, bajar a **1 min** y buscar **retroceso CONTINUO de 3 velas**
   (continuas, NO intercaladas). **Los dojis cuentan como vela continua a favor.**
5. Trazar **línea de agotamiento en 1 min**.
6. Definir SL en una **zona de riesgo identificada en 1 min** (no desde el módulo).
7. Definir **risk-ratio en 5 min** (SL y TP). Buscar **3:1**, pero el ratio depende del
   espacio de S/R medido en **1H/4H**.
8. Configurar **ATM (ATM Soul)** y el **número de contratos**.
9. **ENTRADA AL MERCADO**, en los **últimos ~10 segundos** de una vela (1 min) que rompe
   con fuerza la línea de agotamiento. El **cuerpo** de la vela debe estar del lado correcto
   de la línea segundos antes del cierre.
10. **Confirmación:** misma dirección/color en 5 min (NO siempre — parámetro ajustable).

## D. Filtros (no se toma el trade si...)
- Está **cerca de un soporte/resistencia** (de alta temporalidad).
- Está **cerca de una noticia fundamental**.
- Mínimo de espacio para al menos **1:1**.

## E. Reglas de sesión y disciplina (CLAVE, nuevas)
- **Máximo 3 entradas por día.**
- **Si la PRIMERA entrada del día da Stop Loss → fuera del mercado ese día.**
- **No** tomar entradas después de las **11:30 AM (Denver)**.
- **Cerrar TODA posición a la 1:00 PM (Denver)** en cuenta de fondeo, vaya como vaya.
- No mantener trades abiertos **solo** durante noticias de **tasa de interés**.
- Reducir apalancamiento tras un mercado tendencial (días siguientes suelen ir en rango).

## F. Gestión / mantenimiento (dejar correr)
- Si cumple todo y está lejos de S/R → buscar **2:1 / 3:1**.
- Al llegar a **1:1** → mover SL a **0.5R** (asegurar 0.5 de ganancia).
- Al llegar a **2:1** → mover SL a **Breakeven**. (Juli anotó "preguntar", queda por confirmar.)
- Con ATM Soul: poner TPs escalonados; al primer TP (mayor # de contratos) dejar SL inicial;
  al segundo TP, mover SL a BE y dejar correr la última porción.
- Escalar contratos (solo en micros) si el precio lateraliza respetando una zona de riesgo
  de 1 min y sigue dando retrocesos de 3 velas continuas.

## G. Plan de salida (cierre manual)
- Si el TP quedó por error después de un S/R → cerrar manual.
- Si el patrón de velas no se cumple y cambia de dirección en 5 y 15 min → cerrar manual.
- Si hay noticia de alta volatilidad no detectada → cerrar **1h antes** de:
  "Fed Chair Powell Speaks" / "Federal Funds Rate".
- Si hace **fallo de máximos en 15 min** y rompe la línea de tendencia → cerrar manual.

## H. Otra estrategia mencionada — "La Nube" (NO es Agotamiento)
- "Nube" = **3 EMAs** (en TradingView; falta confirmar cuáles con Cris).
- Entrada en **30 seg**; velas pueden ser intermitentes (no continuas).
- **Todas las temporalidades sincronizadas** (1H / 15m / 2m / 30s): la nube por encima
  (largo) o por debajo (corto) del precio, sin importar el color.
- Corto = "J invertida". SL por encima/debajo de la nube en 30s.
- Ratio 1:1 → 2:1 protegiendo el 1:1. Cobrar 1:1 en minis = ~$300.
- **Solo sesión americana; las noticias no importan.**

## I. Pendientes / por conseguir (de Juli)
- Foto del cuadro de **patrones de velas** de Cris.
- Qué **EMAs** son la "nube".
- **Estudio universitario 2021** sobre la estrategia que mencionó Cris.
- Replicador de cuentas: **Bruno Meza** (brunomeza.com, cupón TFT) — sirve para My Funded Futures.

