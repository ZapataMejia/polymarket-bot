# Test Plan — Sistema de Trading Automatizado con IA

## Objetivo
Construir un sistema de trading algorítmico que recolecte datos de mercado,
genere señales con ML/RL, ejecute trades automáticamente y gestione riesgo,
validado en cada fase antes de avanzar a la siguiente.

---

## FASE 0 — Infraestructura Base

| # | Test | Criterio de Éxito | Tipo |
|---|------|--------------------|------|
| 0.1 | Importar todos los módulos del proyecto | Sin errores de importación | Unit |
| 0.2 | Cargar configuración desde YAML | Config object con valores correctos | Unit |
| 0.3 | Logger escribe a archivo y consola | Archivo de log creado, formato correcto | Unit |
| 0.4 | Base de datos inicializa tablas | Tablas `trades`, `signals`, `portfolio` existen | Unit |
| 0.5 | Variables de entorno cargadas desde .env | API keys accesibles sin hardcodear | Unit |

---

## FASE 1 — Data Pipeline

| # | Test | Criterio de Éxito | Tipo |
|---|------|--------------------|------|
| 1.1 | Conexión REST a Binance (público) | Respuesta 200, JSON válido | Integration |
| 1.2 | Obtener OHLCV histórico BTC/USDT | DataFrame con columnas [timestamp, O, H, L, C, V], >1000 rows | Integration |
| 1.3 | Obtener OHLCV multi-timeframe (1m, 5m, 15m, 1h, 4h) | 5 DataFrames válidos por timeframe | Integration |
| 1.4 | Obtener orderbook L2 | Bids y asks con profundidad >= 20 niveles | Integration |
| 1.5 | WebSocket streaming de trades en tiempo real | Recibir >= 10 trades en 5 segundos | Integration |
| 1.6 | Guardar datos en Parquet | Archivo .parquet legible, schema correcto | Unit |
| 1.7 | Cargar datos históricos desde Parquet | DataFrame idéntico al guardado | Unit |
| 1.8 | Rate limiting respetado | Sin errores 429 en 100 requests consecutivos | Integration |
| 1.9 | Reconexión automática WebSocket | Tras desconexión simulada, reconecta en <10s | Integration |
| 1.10 | Multi-symbol data collection | Datos simultáneos BTC, ETH, SOL | Integration |

---

## FASE 2 — Feature Engineering

| # | Test | Criterio de Éxito | Tipo |
|---|------|--------------------|------|
| 2.1 | Calcular RSI(14) | Valores entre 0-100, matches con referencia | Unit |
| 2.2 | Calcular MACD(12,26,9) | Signal, MACD, histogram correctos | Unit |
| 2.3 | Calcular Bollinger Bands(20,2) | Upper > Middle > Lower, width > 0 | Unit |
| 2.4 | Calcular ATR(14) | Valores positivos, correlación con volatilidad | Unit |
| 2.5 | Calcular VWAP | Valor entre high y low del día | Unit |
| 2.6 | Hurst Exponent | Valor entre 0-1, >0.5 trending, <0.5 mean-reverting | Unit |
| 2.7 | Volatilidad realizada (20 períodos) | Valor positivo, correlación con ATR | Unit |
| 2.8 | Order flow imbalance | Valor entre -1 y 1 | Unit |
| 2.9 | Feature pipeline completo | DataFrame con 50+ columnas, sin NaN (excepto warmup) | Unit |
| 2.10 | Normalización z-score | Media ≈ 0, std ≈ 1 para features normalizados | Unit |

---

## FASE 3 — Estrategias Clásicas + Backtesting

| # | Test | Criterio de Éxito | Tipo |
|---|------|--------------------|------|
| 3.1 | Backtest engine genera trades | Lista de trades con entry/exit/pnl | Unit |
| 3.2 | Métricas de performance correctas | Sharpe, Sortino, max drawdown, win rate calculados | Unit |
| 3.3 | Mean Reversion strategy | Genera señales en mercado lateral (Sharpe > 0) | Backtest |
| 3.4 | Trend Following strategy | Genera señales en mercado trending (Sharpe > 0) | Backtest |
| 3.5 | Breakout strategy | Detecta rupturas de rango correctamente | Backtest |
| 3.6 | Strategy no mira al futuro | Resultados idénticos con y sin datos futuros | Unit |
| 3.7 | Comisiones incluidas | PnL neto < PnL bruto en todos los trades | Unit |
| 3.8 | Slippage modelado | Resultados con slippage peores que sin slippage | Unit |
| 3.9 | Walk-forward validation | Out-of-sample Sharpe > 0 para al menos 1 estrategia | Backtest |
| 3.10 | Comparación vs Buy & Hold | Reporte generado con métricas comparativas | Backtest |

---

## FASE 4 — Machine Learning

| # | Test | Criterio de Éxito | Tipo |
|---|------|--------------------|------|
| 4.1 | XGBoost entrena sin errores | Modelo guardado, accuracy > random (>34% en 3-class) | Unit |
| 4.2 | LightGBM entrena sin errores | Modelo guardado, accuracy > random | Unit |
| 4.3 | LSTM entrena sin errores | Loss decrece por epoch, modelo guardado | Unit |
| 4.4 | Walk-forward split correcto | Train siempre antes de test en tiempo | Unit |
| 4.5 | Feature importance calculada | Top 10 features identificados | Unit |
| 4.6 | Modelo no overfitea | Gap train/test accuracy < 15% | Unit |
| 4.7 | Sentiment score generado | Score entre -1 y 1 para texto de prueba | Unit |
| 4.8 | Ensemble de modelos | Combinación mejora Sharpe vs modelos individuales | Backtest |
| 4.9 | Modelo serializable | Guardar/cargar produce predicciones idénticas | Unit |
| 4.10 | Predicción en tiempo real | Latencia < 100ms por predicción | Unit |

---

## FASE 5 — Reinforcement Learning

| # | Test | Criterio de Éxito | Tipo |
|---|------|--------------------|------|
| 5.1 | Environment Gym funcional | Reset y step sin errores, observation shape correcto | Unit |
| 5.2 | PPO agent entrena | Reward promedio incrementa over episodes | Unit |
| 5.3 | SAC agent entrena | Reward promedio incrementa over episodes | Unit |
| 5.4 | Agent aprende a no perder | Drawdown < 50% en test set después de 100K steps | Backtest |
| 5.5 | Agent supera buy & hold | Sharpe ratio > buy & hold en out-of-sample | Backtest |
| 5.6 | Action space válido | Solo genera acciones legales (no short si no se permite) | Unit |
| 5.7 | Reward shaping funciona | Agent con Sharpe reward > agent con profit-only reward | Backtest |
| 5.8 | Checkpoint/resume entrenamiento | Continuar training produce mejores resultados | Unit |

---

## FASE 6 — MetaTrader 5 Integration

| # | Test | Criterio de Éxito | Tipo |
|---|------|--------------------|------|
| 6.1 | Conexión MT5 exitosa | mt5.initialize() retorna True | Integration |
| 6.2 | Obtener datos de mercado | OHLCV de EUR/USD disponible | Integration |
| 6.3 | Abrir orden demo | Orden de compra ejecutada en cuenta demo | Integration |
| 6.4 | Cerrar orden demo | Posición cerrada correctamente | Integration |
| 6.5 | Modificar SL/TP | Stop loss y take profit actualizados | Integration |
| 6.6 | Estrategia funciona en MT5 | Misma estrategia genera señales consistentes crypto ↔ forex | Integration |
| 6.7 | Manejo de errores MT5 | Errores de conexión capturados sin crash | Unit |

---

## FASE 7 — Risk Management

| # | Test | Criterio de Éxito | Tipo |
|---|------|--------------------|------|
| 7.1 | Position sizing Kelly | Tamaño calculado < 100% del capital | Unit |
| 7.2 | Stop loss ATR-based | SL ajustado a volatilidad actual | Unit |
| 7.3 | Circuit breaker activa | Trading se detiene si drawdown > umbral diario | Unit |
| 7.4 | Max posiciones respetado | Orden rechazada si ya hay N posiciones abiertas | Unit |
| 7.5 | Correlación check | Orden rechazada si correlación > 0.8 con posición existente | Unit |
| 7.6 | Max loss diario | Trading se detiene al alcanzar pérdida máxima del día | Unit |
| 7.7 | Risk per trade máximo | Ningún trade arriesga más de X% del capital | Unit |

---

## FASE 8 — Paper Trading

| # | Test | Criterio de Éxito | Tipo |
|---|------|--------------------|------|
| 8.1 | Dry-run 24h sin crash | Sistema opera 24h continuas sin errores | Integration |
| 8.2 | Trades registrados en DB | Todos los trades con timestamp, price, size, pnl | Integration |
| 8.3 | PnL tracking correcto | PnL acumulado coincide con suma de trades | Unit |
| 8.4 | Alertas Telegram funcionan | Notificación recibida al abrir/cerrar trade | Integration |
| 8.5 | Performance 7 días | Sharpe > 0 en 7 días de paper trading | Validation |
| 8.6 | Performance 30 días | Sharpe > 0.5 en 30 días, drawdown < 20% | Validation |
| 8.7 | Comparación vs benchmark | Report generado comparando vs buy & hold, SPY | Validation |
| 8.8 | Degradación detectada | Sistema alerta si Sharpe cae bajo umbral | Integration |

---

## FASE 9 — Producción

| # | Test | Criterio de Éxito | Tipo |
|---|------|--------------------|------|
| 9.1 | Docker build exitoso | Container arranca sin errores | Integration |
| 9.2 | VPS deploy funciona | Bot corriendo en servidor remoto | Integration |
| 9.3 | Auto-restart on crash | Systemd/supervisor reinicia el proceso | Integration |
| 9.4 | Daily report generado | Email/Telegram con resumen diario a las 00:00 UTC | Integration |
| 9.5 | Dashboard accesible | Streamlit UI muestra trades, PnL, métricas en tiempo real | Integration |
| 9.6 | Escalado gradual | Capital incrementa automáticamente si métricas se mantienen | Validation |
| 9.7 | Kill switch funciona | Bot se detiene completamente con un comando Telegram | Integration |

---

## Criterios de Avance entre Fases

| Transición | Gate |
|-----------|------|
| Fase 0 → 1 | Todos los tests 0.x pasan |
| Fase 1 → 2 | Pipeline recolectando datos sin errores por 1 hora |
| Fase 2 → 3 | Feature pipeline genera DataFrame completo sin NaN |
| Fase 3 → 4 | Al menos 1 estrategia con Sharpe > 0 en walk-forward |
| Fase 4 → 5 | ML model mejora Sharpe vs estrategia clásica base |
| Fase 5 → 6 | RL agent supera mejor estrategia ML en out-of-sample |
| Fase 6 → 7 | Trades ejecutándose correctamente en MT5 demo |
| Fase 7 → 8 | Risk engine bloquea trades que violan límites |
| Fase 8 → 9 | 30 días de paper trading con Sharpe > 0.5 |
