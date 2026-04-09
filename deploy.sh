#!/bin/bash
# ============================================================
# HTML Hub 一键部署脚本
# 适用于 Ubuntu 20.04+ / Debian 11+
# 用法: sudo bash deploy.sh your-domain.com
# ============================================================

set -e

DOMAIN="${1:?请提供域名，用法: sudo bash deploy.sh your-domain.com}"
APP_DIR="/opt/htmlhub"
APP_USER="www-data"
PYTHON="python3"
ADMIN_TOKEN="$(grep '^admin_token:' "$APP_DIR/config.yaml" | head -1 | sed -E 's/^admin_token:[[:space:]]*"(.*)"/\1/' | sed -E "s/^admin_token:[[:space:]]*'?([^']*)'?$/\1/")"

echo "============================================"
echo "  HTML Hub 部署脚本"
echo "  域名: $DOMAIN"
echo "============================================"

# ----------------------------------------------------------
# 1. 安装系统依赖
# ----------------------------------------------------------
echo ""
echo ">>> [1/6] 安装系统依赖..."
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx python3-pip python3-venv > /dev/null

# ----------------------------------------------------------
# 2. 安装 Python 依赖
# ----------------------------------------------------------
echo ">>> [2/6] 安装 Python 依赖..."
cd "$APP_DIR"
$PYTHON -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
deactivate

# ----------------------------------------------------------
# 3. 更新 config.yaml 中的 base_url
# ----------------------------------------------------------
echo ">>> [3/6] 更新配置..."
if grep -q "base_url:" "$APP_DIR/config.yaml"; then
    sed -i "s|base_url:.*|base_url: \"https://$DOMAIN\"|" "$APP_DIR/config.yaml"
    echo "    base_url 已更新为 https://$DOMAIN"
fi

# 确保 data 和 pages 目录存在且权限正确
mkdir -p "$APP_DIR/data" "$APP_DIR/pages"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR/data" "$APP_DIR/pages"

# ----------------------------------------------------------
# 4. 配置 Nginx
# ----------------------------------------------------------
echo ">>> [4/6] 配置 Nginx..."
cat > "/etc/nginx/sites-available/htmlhub" <<NGINX
server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8888;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/htmlhub /etc/nginx/sites-enabled/htmlhub
# 移除默认站点（如果存在）
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx
echo "    Nginx 配置完成"

# ----------------------------------------------------------
# 5. 申请 Let's Encrypt SSL 证书
# ----------------------------------------------------------
echo ">>> [5/6] 申请 SSL 证书..."
echo "    请确保域名 $DOMAIN 已解析到本服务器 IP"
echo ""
certbot --nginx \
    -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --register-unsafely-without-email \
    --redirect

echo "    SSL 证书申请成功！"
echo "    证书自动续期已配置（certbot 自带 systemd timer）"

# ----------------------------------------------------------
# 6. 配置 systemd 服务
# ----------------------------------------------------------
echo ">>> [6/6] 配置 systemd 服务..."
cat > /etc/systemd/system/htmlhub.service <<SYSTEMD
[Unit]
Description=HTML Hub - AI HTML Page Sharing Server
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable htmlhub
systemctl restart htmlhub

echo ""
echo "============================================"
echo "  部署完成！"
echo "============================================"
echo ""
echo "  网站地址:    https://$DOMAIN"
if [ -n "$ADMIN_TOKEN" ]; then
    echo "  Dashboard:   https://$DOMAIN/dashboard?token=$ADMIN_TOKEN"
    echo "  Super Admin Token: $ADMIN_TOKEN"
else
    echo "  Dashboard:   https://$DOMAIN/dashboard?token=<your-admin-token>"
fi
echo ""
echo "  管理命令:"
echo "    查看状态:  systemctl status htmlhub"
echo "    查看日志:  journalctl -u htmlhub -f"
echo "    重启服务:  systemctl restart htmlhub"
echo ""
echo "  SSL 证书会自动续期，无需手动操作。"
echo "  如需手动续期: certbot renew"
echo ""
echo "  请妥善保存 config.yaml 中的密钥配置。"
echo "============================================"
