#!/bin/bash

# Script de déploiement automatique pour le solveur Bitcoin Puzzle
# Ce script automatise la création et le déploiement sur plusieurs Cloud Shell

set -e

# Configuration par défaut
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARY_NAME="bitcoin_puzzle_solver"
CONFIG_FILE="instances.json"
PUZZLE_FILE="puzzle.txt"
PROJECTS_FILE="projects.txt"
LOG_FILE="deployment.log"

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Fonction de logging
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"
}

error() {
    echo -e "${RED}[ERREUR]${NC} $1" | tee -a "$LOG_FILE"
}

warning() {
    echo -e "${YELLOW}[AVERTISSEMENT]${NC} $1" | tee -a "$LOG_FILE"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"
}

# Vérifier les prérequis
check_prerequisites() {
    log "Vérification des prérequis..."
    
    # Vérifier gcloud
    if ! command -v gcloud &> /dev/null; then
        error "gcloud CLI n'est pas installé. Veuillez l'installer : https://cloud.google.com/sdk/docs/install"
        exit 1
    fi
    
    # Vérifier l'authentification
    if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" &> /dev/null; then
        error "Vous n'êtes pas authentifié avec gcloud. Exécutez : gcloud auth login"
        exit 1
    fi
    
    # Vérifier les fichiers requis
    if [[ ! -f "$BINARY_NAME" ]]; then
        error "Binaire '$BINARY_NAME' non trouvé dans le répertoire courant"
        exit 1
    fi
    
    if [[ ! -f "$PUZZLE_FILE" ]]; then
        warning "Fichier puzzle '$PUZZLE_FILE' non trouvé, création d'un exemple..."
        create_example_puzzle
    fi
    
    log "Prérequis vérifiés avec succès"
}

# Créer un fichier puzzle d'exemple
create_example_puzzle() {
    cat > "$PUZZLE_FILE" << 'EOF'
# Fichier puzzle d'exemple - Remplacez par vos vraies adresses Bitcoin
# Puzzle #64 (résolu) - pour test uniquement
16jY7qLJnxb7CHZyqBP8qca9d51gAjyXQN
# Puzzle #65 (non résolu)
1BY8GQbnueYofwSuFAT3USAhGjPrkxDdW9
# Puzzle #66 (non résolu) 
13zb1hQbWVsc2S7ZTZnP2G4undNNpdh5so
# Ajoutez vos adresses ici...
EOF
    info "Fichier puzzle d'exemple créé : $PUZZLE_FILE"
}

# Créer le fichier de configuration des projets
create_projects_config() {
    if [[ ! -f "$PROJECTS_FILE" ]]; then
        log "Création du fichier de configuration des projets..."
        cat > "$PROJECTS_FILE" << 'EOF'
# Liste des projets Google Cloud à utiliser
# Un projet par ligne, format: PROJECT_ID:ZONE:USERNAME
# Exemple:
# mon-projet-1:us-central1-a:cloudshell
# mon-projet-2:europe-west1-b:cloudshell
# mon-projet-3:asia-southeast1-a:cloudshell

# Remplacez par vos vrais IDs de projet
EOF
        warning "Fichier '$PROJECTS_FILE' créé. Veuillez le remplir avec vos IDs de projet Google Cloud."
        return 1
    fi
    return 0
}

# Lire la configuration des projets
read_projects_config() {
    if ! create_projects_config; then
        return 1
    fi
    
    local projects=()
    while IFS= read -r line; do
        # Ignorer les commentaires et lignes vides
        if [[ "$line" =~ ^[[:space:]]*# ]] || [[ -z "${line// }" ]]; then
            continue
        fi
        projects+=("$line")
    done < "$PROJECTS_FILE"
    
    if [[ ${#projects[@]} -eq 0 ]]; then
        error "Aucun projet configuré dans $PROJECTS_FILE"
        return 1
    fi
    
    printf '%s\n' "${projects[@]}"
}

# Générer le fichier instances.json
generate_instances_config() {
    log "Génération du fichier de configuration des instances..."
    
    local projects
    if ! projects=($(read_projects_config)); then
        return 1
    fi
    
    local instances_json="["
    local first=true
    local instance_count=0
    
    for project_config in "${projects[@]}"; do
        IFS=':' read -r project_id zone username <<< "$project_config"
        
        # Valeurs par défaut si non spécifiées
        zone=${zone:-"us-central1-a"}
        username=${username:-"cloudshell"}
        
        if [[ -z "$project_id" ]]; then
            warning "ID de projet vide ignoré : $project_config"
            continue
        fi
        
        if [[ "$first" == true ]]; then
            first=false
        else
            instances_json+=","
        fi
        
        ((instance_count++))
        instances_json+="
    {
        \"name\": \"puzzle-solver-$instance_count\",
        \"project_id\": \"$project_id\",
        \"zone\": \"$zone\",
        \"username\": \"$username\"
    }"
    done
    
    instances_json+="
]"
    
    echo "$instances_json" > "$CONFIG_FILE"
    log "Configuration générée pour $instance_count instances dans $CONFIG_FILE"
    return 0
}

# Activer les APIs nécessaires pour un projet
enable_apis() {
    local project_id="$1"
    info "Activation des APIs pour le projet $project_id..."
    
    local apis=(
        "cloudshell.googleapis.com"
        "compute.googleapis.com"
        "cloudbuild.googleapis.com"
    )
    
    for api in "${apis[@]}"; do
        info "Activation de $api..."
        if gcloud services enable "$api" --project="$project_id" 2>/dev/null; then
            info "✓ $api activée"
        else
            warning "Impossible d'activer $api (peut-être déjà activée)"
        fi
    done
}

# Déployer sur une instance Cloud Shell
deploy_to_instance() {
    local project_id="$1"
    local instance_name="$2"
    local zone="$3"
    
    info "Déploiement sur $instance_name ($project_id)..."
    
    # Activer les APIs nécessaires
    enable_apis "$project_id"
    
    # Vérifier si Cloud Shell est disponible
    info "Vérification de l'accès à Cloud Shell..."
    local test_cmd="gcloud cloud-shell ssh --project=$project_id --command='echo Cloud Shell accessible' 2>/dev/null"
    if ! eval "$test_cmd"; then
        warning "Cloud Shell non accessible pour $project_id, tentative d'initialisation..."
        
        # Essayer de créer l'environnement Cloud Shell
        if ! gcloud cloud-shell environments create --project="$project_id" 2>/dev/null; then
            warning "Impossible d'initialiser Cloud Shell pour $project_id"
            return 1
        fi
        
        # Attendre que l'environnement soit prêt
        sleep 10
    fi
    
    # Créer le répertoire de travail
    info "Préparation de l'environnement..."
    gcloud cloud-shell ssh --project="$project_id" --command="mkdir -p ~/bitcoin_solver" || true
    
    # Uploader les fichiers
    info "Upload des fichiers..."
    local files_to_upload=("$BINARY_NAME" "$PUZZLE_FILE")
    
    for file in "${files_to_upload[@]}"; do
        info "Upload de $file..."
        if gcloud cloud-shell scp "$file" "cloudshell:~/bitcoin_solver/" --project="$project_id"; then
            info "✓ $file uploadé"
        else
            error "Échec upload de $file vers $project_id"
            return 1
        fi
    done
    
    # Rendre le binaire exécutable
    info "Configuration des permissions..."
    gcloud cloud-shell ssh --project="$project_id" --command="chmod +x ~/bitcoin_solver/$BINARY_NAME"
    
    # Créer un script de démarrage
    info "Création du script de démarrage..."
    local startup_script="#!/bin/bash
cd ~/bitcoin_solver
echo 'Bitcoin Puzzle Solver déployé avec succès!'
echo 'Pour démarrer manuellement: ./$BINARY_NAME'
echo 'Logs disponibles dans: ~/bitcoin_solver/solver.log'
"
    
    echo "$startup_script" | gcloud cloud-shell ssh --project="$project_id" --command="cat > ~/bitcoin_solver/start.sh && chmod +x ~/bitcoin_solver/start.sh"
    
    log "✓ Déploiement réussi sur $instance_name"
    return 0
}

# Déploiement sur toutes les instances
deploy_all() {
    log "Début du déploiement sur toutes les instances..."
    
    local projects
    if ! projects=($(read_projects_config)); then
        return 1
    fi
    
    local success_count=0
    local total_count=${#projects[@]}
    
    for project_config in "${projects[@]}"; do
        IFS=':' read -r project_id zone username <<< "$project_config"
        zone=${zone:-"us-central1-a"}
        
        local instance_name="puzzle-solver-$((success_count + 1))"
        
        if deploy_to_instance "$project_id" "$instance_name" "$zone"; then
            ((success_count++))
        else
            error "Échec du déploiement sur $project_id"
        fi
        
        # Pause entre les déploiements pour éviter les limites de taux
        sleep 2
    done
    
    log "Déploiement terminé : $success_count/$total_count instances réussies"
    
    if [[ $success_count -gt 0 ]]; then
        info "Vous pouvez maintenant lancer le coordinateur avec : python3 coordinator.py"
    fi
}

# Test de connectivité
test_connectivity() {
    log "Test de connectivité vers toutes les instances..."
    
    local projects
    if ! projects=($(read_projects_config)); then
        return 1
    fi
    
    for project_config in "${projects[@]}"; do
        IFS=':' read -r project_id zone username <<< "$project_config"
        
        info "Test de connexion à $project_id..."
        if gcloud cloud-shell ssh --project="$project_id" --command="echo 'Connexion OK'" 2>/dev/null; then
            log "✓ $project_id accessible"
        else
            warning "✗ $project_id non accessible"
        fi
    done
}

# Nettoyage des instances
cleanup() {
    log "Nettoyage des instances..."
    
    local projects
    if ! projects=($(read_projects_config)); then
        return 1
    fi
    
    for project_config in "${projects[@]}"; do
        IFS=':' read -r project_id zone username <<< "$project_config"
        
        info "Nettoyage de $project_id..."
        gcloud cloud-shell ssh --project="$project_id" --command="pkill -f bitcoin_puzzle_solver || true; rm -rf ~/bitcoin_solver" 2>/dev/null || true
        log "✓ $project_id nettoyé"
    done
}

# Affichage de l'aide
show_help() {
    cat << EOF
🚀 Script de Déploiement Bitcoin Puzzle Solver

UTILISATION:
    $0 [COMMANDE]

COMMANDES:
    deploy      Déployer sur toutes les instances
    config      Générer le fichier de configuration des instances
    test        Tester la connectivité vers toutes les instances
    cleanup     Nettoyer toutes les instances
    help        Afficher cette aide

FICHIERS DE CONFIGURATION:
    $PROJECTS_FILE    - Liste des projets Google Cloud
    $CONFIG_FILE      - Configuration des instances (généré automatiquement)
    $PUZZLE_FILE      - Adresses Bitcoin à rechercher

EXEMPLE D'UTILISATION:
    1. Modifiez $PROJECTS_FILE avec vos IDs de projet
    2. Lancez: $0 deploy
    3. Lancez le coordinateur: python3 coordinator.py

EOF
}

# Fonction principale
main() {
    echo "🚀 Bitcoin Puzzle Solver - Script de Déploiement"
    echo "================================================="
    
    local command="${1:-help}"
    
    case "$command" in
        "deploy")
            check_prerequisites
            generate_instances_config
            deploy_all
            ;;
        "config")
            generate_instances_config
            ;;
        "test")
            test_connectivity
            ;;
        "cleanup")
            cleanup
            ;;
        "help"|"--help"|"-h")
            show_help
            ;;
        *)
            error "Commande inconnue: $command"
            show_help
            exit 1
            ;;
    esac
}

# Gestion des signaux pour un arrêt propre
trap 'echo -e "\n${YELLOW}Arrêt demandé...${NC}"; exit 0' INT TERM

# Exécution du script
main "$@"