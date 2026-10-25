# Self-Hosting an Open-Weight LLM on DigitalOcean GPU (vLLM)

> **Status**: Documented, not yet demoed.
> Please verify current vLLM and DigitalOcean documentation for exact GPU hardware availability, drivers, and image versions before deploying.

---

## 1. Overview

Auto-Remediate is designed to work with any OpenAI-compatible API endpoint. While Google AI Studio (Gemma 4 via Gemini API) serves as the primary managed endpoint, you can self-host open-weight models (such as Gemma 2 / Gemma 4 / Mistral) on a dedicated GPU Droplet using [vLLM](https://docs.vllm.ai/).

---

## 2. Deploying a DigitalOcean GPU Droplet

1. Log in to the [DigitalOcean Cloud Console](https://cloud.digitalocean.com/).
2. Navigate to **Droplets** > **Create Droplet**.
3. Select **GPU Droplets**:
   - Choose a GPU instance suited to your model size (e.g., NVIDIA H100, A100, or L40S depending on region and availability).
4. Choose an OS Image:
   - Select an Ubuntu LTS base image with NVIDIA CUDA drivers pre-installed, or install NVIDIA drivers and the NVIDIA Container Toolkit.
5. Add your SSH keys and create the Droplet.

---

## 3. Setting Up vLLM

SSH into your GPU Droplet:

```bash
ssh root@<DROPLET_IP>
```

Verify GPU visibility:

```bash
nvidia-smi
```

Install vLLM (check [vLLM installation docs](https://docs.vllm.ai/en/latest/getting_started/installation.html) for recommended Python environment setup):

```bash
python3 -m venv ~/vllm-env
source ~/vllm-env/bin/activate
pip install --upgrade pip
pip install vllm
```

---

## 4. Serving the Model via OpenAI-Compatible API

Launch the vLLM OpenAI-compatible server:

```bash
vllm serve google/gemma-2-9b-it \
    --host 0.0.0.0 \
    --port 8000 \
    --api-key YOUR_SECRET_CUSTOM_KEY
```

> **Note**: For production environments, secure this endpoint behind an HTTPS reverse proxy (such as Caddy or Nginx) and restrict access using a DigitalOcean Cloud Firewall or Tailscale.

---

## 5. Configuring Auto-Remediate

In your Auto-Remediate `.env` file, point `LLM_BASE_URL` to your Droplet:

```env
LLM_BASE_URL=http://<DROPLET_IP>:8000/v1
LLM_MODEL=google/gemma-2-9b-it
LLM_API_KEY=YOUR_SECRET_CUSTOM_KEY
```

Test connectivity using the model gate script:

```bash
python scripts/model_check.py
```
