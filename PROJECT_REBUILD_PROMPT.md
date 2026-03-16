# Drishi Pro — Project Rebuild Prompt (Elite Hybrid v6.1)

This document provides the definitive blueprint for the **v6.1 Elite Engine**.

## Project Purpose
An enterprise-grade AI interview utility featuring an **Intent-Aware 14-Layer Pipeline**. Designed for sub-300ms responses, multi-step topic forecasting, and automatic fail-safe degradation.

## v6.1 Elite Decision Pipeline

1. **Audio Capture**: Pulse/ALSA VAD-based continuous segmenting.
2. **STT Engine**: Stabilized transcription with technical error correction.
3. **Intent Detection Engine**: Classifies Knowledge, Troubleshooting, Architecture, Behavioral, or Coding.
4. **Intent Confidence Scoring**: Fallback to generic strategy if intent confidence < 0.60.
5. **Context Drift Detector**: Rolling 3-question context window + topic inheritance.
6. **Triple-Hybrid Retrieval**: N-gram (35%) + Semantic (35%) + Keyword (30%).
7. **Semantic Cache Confidence**: Requires Similarity > 0.80 AND Intent Match.
8. **Predictive Cache Aging**: Forecasted answers expire after 10 minutes to maintain freshness.
9. **Adaptive Prediction Horizon**: Predicts 1-5 nodes based on topic confidence.
10. **Resume Knowledge Graph v6**: Nodes include project/experience metadata + compressed injection.
11. **Behavioral Coaching Layer**: Automatic STAR formatting with explicit labels.
12. **Auto-Adaptive Model Router**: Intent-aware routing (Knowledge -> Haiku, Complex -> Sonnet).
13. **Fail-Safe Degradation**: 3 levels (Normal, Moderate, Critical) based on system vitals.
14. **Latency Auto-Optimizer**: Throttles prediction depth if LLM latency > 2s.

## Intelligent Features

### 1. Interview Analytics Dashboard
- Real-time visualization of Cache Hit Rate, Intent Accuracy, and Topic Path.
- Telemetry trace logging for post-interview performance review.

### 2. System Health Guardian (Elite Watchdog)
- Throttles resource-intensive features (Sonnet, Prediction) during CPU/RAM spikes.
- Ensures the system remains responsive even under 90%+ system load.

### 3. Automatic Clarification
- Generates clarifying questions if pipeline confidence is critically low (< 0.65).

## Execution Model
- **Concurrency**: High-performance multi-threading (Capture, v6.1 Processor, SSE Server).
- **Latency HUD**: Detailed Stage-by-Stage profiler (STT, SEM, PRE, LLM).
