#!/bin/bash
set -e

REGISTRY="docker.x51.vn/x-ai"
MAKER8_TAG="20260414.2150"
EDITOR8_BACKEND_TAG="20260414.2126"
EDITOR8_FRONTEND_TAG="20260414.2127"

DEPLOYMENT_HOST="10.113.213.9"
DEPLOYMENT_USER="beou"
SSH_KEY="/home/beou/deployment/worker-z440/ssh/id_ed25519"
COMPOSE_DIR="/home/beou/deployment/worker-z440"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 Production Deployment Pipeline"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "📦 Step 1: Push Images to Registry"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Push with retry logic
push_image() {
    local image=$1
    local max_attempts=3
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        echo "Pushing $image (attempt $attempt/$max_attempts)..."
        if docker push "$image"; then
            echo "✅ $image pushed successfully"
            return 0
        fi
        echo "⚠️  Push failed, retrying in 10 seconds..."
        sleep 10
        attempt=$((attempt + 1))
    done
    
    echo "❌ Failed to push $image after $max_attempts attempts"
    return 1
}

push_image "$REGISTRY/maker8:$MAKER8_TAG"
push_image "$REGISTRY/editor8-backend:$EDITOR8_BACKEND_TAG"
push_image "$REGISTRY/editor8-frontend:$EDITOR8_FRONTEND_TAG"

echo ""
echo "🔄 Step 2: Deploy to worker-z440"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# SSH into deployment host and restart services
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "${DEPLOYMENT_USER}@${DEPLOYMENT_HOST}" << EOSSH
    set -e
    cd $COMPOSE_DIR
    
    echo "📥 Pulling latest images..."
    docker-compose pull maker8 editor8-backend editor8-worker editor8-frontend
    
    echo "🔄 Restarting services..."
    docker-compose up -d maker8
    docker-compose up -d editor8-backend editor8-worker editor8-frontend
    
    sleep 3
    echo "✅ Services restarted successfully"
    
    echo ""
    echo "🔍 Service Status:"
    docker-compose ps maker8 editor8-backend editor8-worker editor8-frontend
    
    echo ""
    echo "📊 Health Checks (wait 30s for services to stabilize)..."
    sleep 30
    docker-compose ps maker8 editor8-backend editor8-worker editor8-frontend || true
    
EOSSH

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Deployment Complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📋 Summary:"
echo "  • maker8:           $MAKER8_TAG"
echo "  • editor8-backend:  $EDITOR8_BACKEND_TAG"
echo "  • editor8-worker:   $EDITOR8_BACKEND_TAG"
echo "  • editor8-frontend: $EDITOR8_FRONTEND_TAG"
echo ""
echo "🔗 Access Points:"
echo "  • Frontend:  https://x51.vn"
echo "  • API:       https://api.x51.vn/editor8"
echo "  • Kafka UI:  https://kafka.x51.vn"
echo ""
