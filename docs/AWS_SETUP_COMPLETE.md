# AWS Setup — DONE

**Status:** ✅ Server is live and reachable.
**Date:** July 8, 2026

---

## Your Server

| Field | Value |
|---|---|
| Instance name | `sarvam-server` |
| Instance ID | `i-05e85796194df1410` |
| Region | `ap-south-1` (Mumbai) |
| Zone | ap-south-1a (Zone A) |
| Instance type | `t4g.small` (2 vCPU, 2 GiB RAM, ARM64) |
| OS | Ubuntu 24.04 LTS ARM64 |
| **Elastic IP (permanent)** | **`13.206.20.25`** |
| Storage | 30 GiB gp3 |
| Key pair | `sarvam-server-key` |
| Security group | `sarvam-sg` (`sg-0ca3ffd530d33c80d`) |
| VPC | `vpc-0a35f2e66dbff07e9` (default) |

---

## Network — Ports Open

| Port | Protocol | Source | Purpose |
|---|---|---|---|
| 22 | SSH | My IP only | SSH admin access |
| 80 | HTTP | Anywhere | Public web |
| 443 | HTTPS | Anywhere | Public secure web |
| 8080 | Custom TCP | Anywhere | Open WebUI (temporary) |

---

## SSH Access

**Command template** (paste into your local Mac Terminal):

```bash
# One-time: fix key permissions
chmod 400 ~/Downloads/sarvam-server-key.pem

# Log in
ssh -i ~/Downloads/sarvam-server-key.pem ubuntu@13.206.20.25
```

If it prompts about authenticity, type `yes` and press Enter.

---

## Cost estimate

| Item | Monthly |
|---|---|
| EC2 `t4g.small` (24×7) | ~$12.20 (free tier eligible until Dec 2026) |
| 30 GiB gp3 storage | ~$2.40 (free tier includes 30 GiB) |
| Elastic IP (attached) | $0 (free while attached) |
| Data transfer (est) | ~$1.00 |
| **Total** | **~$0–15/month** in year 1, ~$15/month after |

---

## What's next (Step 5 — Docker install)

Once SSH tested, install Docker + docker-compose:

```bash
# Update packages
sudo apt update && sudo apt upgrade -y

# Install Docker
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Let ubuntu user run docker without sudo
sudo usermod -aG docker ubuntu

# Verify
docker --version
docker compose version
```

---

## Subdomain (deferred)

`sarvam.inspiritvision.com` → `13.206.20.25`

To add later, we'll create an A record at whatever service manages `inspiritvision.com` DNS.

---

## Rotation reminder

Old IAM access key `AKIAWIRYYQK6HHTMC5W7` still in vault, safe but unused. Delete when convenient at IAM → Users → sarvam → Security credentials.
