# Enabling real vLLM (when a GPU machine is available)

vLLM is **PARKED** until GPU hardware exists. Enabling it is **CONFIG ONLY** — no code changes.
Owner: AI team (model choice + weights). DevOps: provides the slot below.

## Prerequisites

- A GPU host with enough VRAM for the chosen model.
- `nvidia-container-toolkit` installed on that host.
- Model weights pre-downloaded into `deploy/models/` (offline prod cannot download them at runtime).

## Steps

1. Uncomment the `vllm:` service block in `deploy/docker-compose.yml`.
2. **Pin an exact `vllm/vllm-openai` image tag/digest** (don't ship `latest` — record the pinned value in this file once chosen).
3. In `deploy/.env` set:
   ```
   USE_MOCK_LLM=0
   OPENAI_BASE_URL=http://vllm:8000/v1
   OPENAI_API_KEY=dummy
   MODEL_NAME=<chosen model>
   ```
4. `docker compose up -d vllm` — wait for weights to load (can take several minutes).
5. Confirm healthy: `docker compose ps vllm` → status `healthy`.

## Test — the real M6 gate

```bash
curl "http://localhost:8080/api/ask/?q=hello"
```

Expected: a **real model reply**, not the mock string `"This is a mock response to: hello"`.

Run the AI Specialist's golden-dataset evaluation against vLLM and confirm it meets the acceptance threshold.

## Architecture notes

- vLLM stays **internal** (`expose`, never `ports`) — Pattern A. The AI service is the only caller.
- The AI service reads `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `MODEL_NAME` from env already — no code changes needed anywhere when switching from mock/OpenAI to vLLM.
- `start_period: 300s` in the healthcheck gives vLLM a grace window while weights load before failures count.

## GPU contract — fill in when hardware is confirmed

| Field                  | Value |
|------------------------|-------|
| Image tag/digest       | TBD   |
| CUDA version           | TBD   |
| Driver version         | TBD   |
| Minimum VRAM required  | TBD   |
| Model name             | TBD   |
| Weights source / path  | TBD   |
