#!/bin/bash
set -e

DEPLOYMENT_HOST="10.113.213.9"
DEPLOYMENT_USER="beou"
SSH_KEY="/home/<user>/deployment/worker-z440/ssh/id_ed25519"
COMPOSE_DIR="/home/<user>/deployment/worker-z440"
TMP_DIR="/tmp"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 Direct Deployment Pipeline (Registry-Bypass)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "📤 Step 1: Transfer Images to worker-z440"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Transfer image files via SCP
echo "Transferring maker8 image..."
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$TMP_DIR/maker8-20260414.2150.tar.gz" \
    "${DEPLOYMENT_USER}@${DEPLOYMENT_HOST}:$COMPOSE_DIR/images/"

echo "Transferring editor8-backend image..."
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$TMP_DIR/editor8-backend-20260414.2126.tar.gz" \
    "${DEPLOYMENT_USER}@${DEPLOYMENT_HOST}:$COMPOSE_DIR/images/"

echo "Transferring editor8-frontend image..."
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$TMP_DIR/editor8-frontend-20260414.2127.tar.gz" \
    "${DEPLOYMENT_USER}@${DEPLOYMENT_HOST}:$COMPOSE_DIR/images/"

echo "✅ Images transferred"

echo ""
echo "🔄 Step 2: Load & Deploy on worker-z440"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# SSH into deployment host and load images
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "${DEPLOYMENT_USER}@${DEPLOYMENT_HOST}" << EOSSH
    set -e
    cd $COMPOSE_DIR
    
    echo "📥 Loading maker8 image..."
    docker load -i images/maker8-20260414.2150.tar.gz
    
    echo "📥 Loading editor8-backend image..."
    docker load -i images/editor8-backend-20260414.2126.tar.gz
    
    echo "📥 Loading editor8-frontend image..."
    docker load -i images/editor8-frontend-20260414.2127.tar.gz
    
    echo "✅ All images loaded"
    
    echo ""
    echo "🔄 Restarting services..."
    
    # Stop old services
    docker-compose down maker8 editor8-backend editor8-worker editor8-frontend 2>/dev/null || true
    sleep 2
    
    # Start new services
    docker-compose up -d maker8
    docker-compose up -d editor8-backend editor8-worker editor8-frontend
    
    sleep 5
    echo "✅ Services restarted successfully"
    
    echo ""
    echo "🔍 Service Status:"
    docker-compose ps maker8 editor8-backend editor8-worker editor8-frontend
    
    echo ""
    echo "📊 Waiting for health checks (30s)..."
    sleep 30
    docker-compose ps maker8 editor8-backend editor8-worker editor8-frontend || true
    
    # Cleanup old image files
    rm -f images/*.tar.gz
    
EOSSH

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Direct Deployment Complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📋 Deployed Versions:"
echo "  • maker8:           20260414.2150"
echo "  • editor8-backend:  20260414.2126"
echo "  • editor8-worker:   20260414.2126"
echo "  • editor8-frontend: 20260414.2127"
echo ""
echo "🔗 Access Points:"
echo "  • Frontend:  https://x51.vn"
echo "  • API:       https://<api-host>/editor8"
echo "  • Kafka UI:  https://<kafka-ui-host>"
echo ""
