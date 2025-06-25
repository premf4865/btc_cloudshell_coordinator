#!/usr/bin/env python3
"""
Coordinateur pour distribuer le solveur de puzzle Bitcoin sur plusieurs instances Cloud Shell
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Optional
import paramiko
import threading
import queue

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('coordinator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class CloudShellInstance:
    """Représente une instance Cloud Shell"""
    name: str
    project_id: str
    zone: str
    username: str
    status: str = "inactive"
    ssh_client: Optional[paramiko.SSHClient] = None
    process_id: Optional[str] = None
    start_range: str = ""
    end_range: str = ""
    keys_per_second: float = 0.0
    total_keys_checked: int = 0
    last_update: datetime = None
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.last_update is None:
            self.last_update = datetime.now()

@dataclass
class CoordinatorConfig:
    """Configuration du coordinateur"""
    binary_name: str = "bitcoin_puzzle_solver"
    puzzle_file: str = "puzzle.txt"
    config_template: str = "config_template.txt"
    total_start_range: str = "0x20000000000000000"
    total_end_range: str = "0x3ffffffffffffffff"
    switch_interval: int = 1000000
    mode: str = "smart"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    max_instances: int = 50
    health_check_interval: int = 30
    rebalance_interval: int = 300

class RangeManager:
    """Gestionnaire des plages de recherche"""
    
    def __init__(self, total_start: str, total_end: str):
        self.total_start = int(total_start, 16 if total_start.startswith('0x') else 10)
        self.total_end = int(total_end, 16 if total_end.startswith('0x') else 10)
        self.assigned_ranges = {}
        self.completed_ranges = set()
        self.lock = threading.Lock()
    
    def get_range_for_instance(self, instance_name: str, num_instances: int) -> tuple:
        """Attribue une plage de recherche à une instance"""
        with self.lock:
            total_range = self.total_end - self.total_start
            range_size = total_range // num_instances
            
            # Calculer l'index de l'instance
            instance_hash = hash(instance_name) % num_instances
            
            start = self.total_start + (instance_hash * range_size)
            end = start + range_size - 1
            
            if instance_hash == num_instances - 1:
                end = self.total_end
            
            self.assigned_ranges[instance_name] = (start, end)
            return (hex(start), hex(end))
    
    def mark_range_completed(self, instance_name: str):
        """Marque une plage comme terminée"""
        with self.lock:
            if instance_name in self.assigned_ranges:
                self.completed_ranges.add(self.assigned_ranges[instance_name])

class CloudShellCoordinator:
    """Coordinateur principal pour gérer les instances Cloud Shell"""
    
    def __init__(self, config: CoordinatorConfig):
        self.config = config
        self.instances: Dict[str, CloudShellInstance] = {}
        self.range_manager = RangeManager(config.total_start_range, config.total_end_range)
        self.status_queue = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=20)
        self.running = False
        
    def add_instance(self, name: str, project_id: str, zone: str = "us-central1-a", username: str = None):
        """Ajoute une nouvelle instance Cloud Shell"""
        if username is None:
            username = os.getenv('USER', 'cloudshell')
        
        instance = CloudShellInstance(
            name=name,
            project_id=project_id,
            zone=zone,
            username=username
        )
        
        self.instances[name] = instance
        logger.info(f"Instance ajoutée: {name} (projet: {project_id})")
        
    def create_cloud_shell_instance(self, project_id: str, instance_name: str) -> bool:
        """Crée une nouvelle instance Cloud Shell via gcloud"""
        try:
            # Activer Cloud Shell API si nécessaire
            cmd_enable = [
                "gcloud", "services", "enable", "cloudshell.googleapis.com",
                "--project", project_id
            ]
            subprocess.run(cmd_enable, check=True, capture_output=True)
            
            # Créer l'environnement Cloud Shell
            cmd_create = [
                "gcloud", "cloud-shell", "environments", "create",
                "--project", project_id
            ]
            result = subprocess.run(cmd_create, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"Instance Cloud Shell créée: {instance_name}")
                return True
            else:
                logger.error(f"Erreur création instance {instance_name}: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Erreur lors de la création de l'instance {instance_name}: {e}")
            return False
    
    def connect_to_instance(self, instance: CloudShellInstance) -> bool:
        """Se connecte à une instance Cloud Shell via SSH"""
        try:
            # Utiliser gcloud pour obtenir les informations de connexion
            cmd = [
                "gcloud", "cloud-shell", "ssh",
                "--project", instance.project_id,
                "--command", "echo 'Connection test'"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                instance.status = "connected"
                logger.info(f"Connexion établie avec {instance.name}")
                return True
            else:
                logger.error(f"Échec connexion {instance.name}: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Erreur connexion {instance.name}: {e}")
            instance.errors.append(f"Connexion error: {str(e)}")
            return False
    
    def upload_files_to_instance(self, instance: CloudShellInstance) -> bool:
        """Upload les fichiers nécessaires vers l'instance"""
        try:
            files_to_upload = [
                self.config.binary_name,
                self.config.puzzle_file,
            ]
            
            for file_path in files_to_upload:
                if os.path.exists(file_path):
                    cmd = [
                        "gcloud", "cloud-shell", "scp",
                        file_path,
                        f"cloudshell:~/{file_path}",
                        "--project", instance.project_id
                    ]
                    
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        logger.error(f"Erreur upload {file_path} vers {instance.name}")
                        return False
            
            logger.info(f"Fichiers uploadés vers {instance.name}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur upload vers {instance.name}: {e}")
            return False
    
    def create_config_for_instance(self, instance: CloudShellInstance) -> str:
        """Crée un fichier de configuration personnalisé pour l'instance"""
        start_range, end_range = self.range_manager.get_range_for_instance(
            instance.name, len(self.instances)
        )
        
        instance.start_range = start_range
        instance.end_range = end_range
        
        config_content = f"""# Configuration générée automatiquement pour {instance.name}
start={start_range}
end={end_range}
cores=0
mode={self.config.mode}
switch_interval={self.config.switch_interval}
subinterval_ratio=0.001
stop_on_find=false
puzzle_file={self.config.puzzle_file}
baby_steps=true
giant_steps=true
bloom_filter=false
smart_jump=true
batch_size=10000
checkpoint_interval=10000000
telegram_bot_token={self.config.telegram_bot_token}
telegram_chat_id={self.config.telegram_chat_id}
"""
        return config_content
    
    def start_solver_on_instance(self, instance: CloudShellInstance) -> bool:
        """Lance le solveur sur une instance"""
        try:
            # Créer le fichier de configuration
            config_content = self.create_config_for_instance(instance)
            
            # Commandes à exécuter
            commands = [
                f"echo '{config_content}' > ~/config.txt",
                f"chmod +x ~/{self.config.binary_name}",
                f"nohup ~/{self.config.binary_name} > ~/solver.log 2>&1 & echo $! > ~/solver.pid"
            ]
            
            for cmd_text in commands:
                cmd = [
                    "gcloud", "cloud-shell", "ssh",
                    "--project", instance.project_id,
                    "--command", cmd_text
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"Erreur commande sur {instance.name}: {result.stderr}")
                    return False
            
            instance.status = "running"
            logger.info(f"Solveur démarré sur {instance.name} (plage: {instance.start_range} - {instance.end_range})")
            return True
            
        except Exception as e:
            logger.error(f"Erreur démarrage solveur sur {instance.name}: {e}")
            return False
    
    def check_instance_status(self, instance: CloudShellInstance):
        """Vérifie le statut d'une instance"""
        try:
            # Vérifier si le processus est toujours actif
            cmd = [
                "gcloud", "cloud-shell", "ssh",
                "--project", instance.project_id,
                "--command", "ps aux | grep bitcoin_puzzle_solver | grep -v grep || echo 'NOT_RUNNING'"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            
            if "NOT_RUNNING" in result.stdout:
                instance.status = "stopped"
            else:
                instance.status = "running"
                
                # Récupérer les statistiques du log
                log_cmd = [
                    "gcloud", "cloud-shell", "ssh",
                    "--project", instance.project_id,
                    "--command", "tail -n 5 ~/solver.log | grep Stats"
                ]
                
                log_result = subprocess.run(log_cmd, capture_output=True, text=True)
                if log_result.stdout:
                    # Parser les statistiques
                    self.parse_statistics(instance, log_result.stdout)
            
            instance.last_update = datetime.now()
            
        except Exception as e:
            logger.error(f"Erreur vérification statut {instance.name}: {e}")
            instance.status = "error"
            instance.errors.append(f"Status check error: {str(e)}")
    
    def parse_statistics(self, instance: CloudShellInstance, log_output: str):
        """Parse les statistiques depuis le log"""
        try:
            for line in log_output.split('\n'):
                if 'Total:' in line and 'Vitesse:' in line:
                    parts = line.split('|')
                    for part in parts:
                        if 'Total:' in part:
                            total_str = part.split('Total:')[1].strip().split()[0]
                            instance.total_keys_checked = int(total_str)
                        elif 'Vitesse:' in part:
                            speed_str = part.split('Vitesse:')[1].strip().split()[0]
                            instance.keys_per_second = float(speed_str)
                    break
        except Exception as e:
            logger.debug(f"Erreur parsing stats pour {instance.name}: {e}")
    
    def display_status(self):
        """Affiche le statut de toutes les instances"""
        os.system('clear')
        print("=" * 80)
        print(f"🚀 COORDINATEUR BITCOIN PUZZLE SOLVER - {datetime.now().strftime('%H:%M:%S')}")
        print("=" * 80)
        
        total_speed = 0
        total_keys = 0
        active_instances = 0
        
        for name, instance in self.instances.items():
            status_icon = {
                "running": "🟢",
                "stopped": "🔴",
                "connected": "🟡",
                "error": "❌",
                "inactive": "⚪"
            }.get(instance.status, "❓")
            
            print(f"{status_icon} {name:20} | {instance.status:10} | "
                  f"{instance.keys_per_second:>8.0f} k/s | "
                  f"{instance.total_keys_checked:>12,} keys | "
                  f"{instance.start_range[:10]}...{instance.end_range[-6:]}")
            
            if instance.status == "running":
                total_speed += instance.keys_per_second
                total_keys += instance.total_keys_checked
                active_instances += 1
        
        print("-" * 80)
        print(f"📊 TOTAL: {active_instances} instances actives | "
              f"{total_speed:.0f} k/s | {total_keys:,} keys vérifiées")
        print("=" * 80)
        
        # Afficher les erreurs récentes
        recent_errors = []
        for instance in self.instances.values():
            if instance.errors:
                recent_errors.extend(instance.errors[-2:])  # 2 dernières erreurs
        
        if recent_errors:
            print("🚨 ERREURS RÉCENTES:")
            for error in recent_errors[-5:]:  # 5 dernières erreurs max
                print(f"   {error}")
            print("-" * 80)
    
    def health_check_loop(self):
        """Boucle de vérification de santé des instances"""
        while self.running:
            try:
                futures = []
                for instance in self.instances.values():
                    if instance.status != "inactive":
                        future = self.executor.submit(self.check_instance_status, instance)
                        futures.append(future)
                
                # Attendre que tous les checks se terminent
                for future in futures:
                    try:
                        future.result(timeout=30)
                    except Exception as e:
                        logger.error(f"Erreur health check: {e}")
                
                time.sleep(self.config.health_check_interval)
                
            except Exception as e:
                logger.error(f"Erreur boucle health check: {e}")
                time.sleep(10)
    
    def restart_failed_instances(self):
        """Redémarre les instances qui ont échoué"""
        for instance in self.instances.values():
            if instance.status in ["stopped", "error"]:
                logger.info(f"Tentative de redémarrage de {instance.name}")
                if self.connect_to_instance(instance):
                    self.start_solver_on_instance(instance)
    
    def auto_scale_instances(self):
        """Ajoute automatiquement des instances si possible"""
        if len(self.instances) < self.config.max_instances:
            # Logique pour créer de nouvelles instances
            # (nécessite une liste de projets disponibles)
            pass
    
    def start_coordination(self):
        """Démarre la coordination"""
        self.running = True
        
        # Démarrer la boucle de health check dans un thread séparé
        health_thread = threading.Thread(target=self.health_check_loop)
        health_thread.daemon = True
        health_thread.start()
        
        # Connecter et démarrer toutes les instances
        logger.info("Démarrage de la coordination...")
        
        for instance in self.instances.values():
            logger.info(f"Initialisation de {instance.name}...")
            if self.connect_to_instance(instance):
                if self.upload_files_to_instance(instance):
                    self.start_solver_on_instance(instance)
        
        # Boucle principale d'affichage
        try:
            while self.running:
                self.display_status()
                time.sleep(5)
                
                # Redémarrer les instances échouées périodiquement
                if int(time.time()) % 60 == 0:
                    self.restart_failed_instances()
                    
        except KeyboardInterrupt:
            logger.info("Arrêt demandé par l'utilisateur")
            self.stop_coordination()
    
    def stop_coordination(self):
        """Arrête la coordination"""
        self.running = False
        logger.info("Arrêt de la coordination...")
        
        # Arrêter tous les solveurs
        for instance in self.instances.values():
            if instance.status == "running":
                try:
                    cmd = [
                        "gcloud", "cloud-shell", "ssh",
                        "--project", instance.project_id,
                        "--command", "pkill -f bitcoin_puzzle_solver"
                    ]
                    subprocess.run(cmd, timeout=10)
                    logger.info(f"Solveur arrêté sur {instance.name}")
                except Exception as e:
                    logger.error(f"Erreur arrêt {instance.name}: {e}")
        
        self.executor.shutdown(wait=True)

def load_instances_from_file(filename: str) -> List[Dict]:
    """Charge la liste des instances depuis un fichier JSON"""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Fichier {filename} non trouvé, création d'un exemple...")
        example_instances = [
            {
                "name": "puzzle-solver-1",
                "project_id": "mon-projet-1",
                "zone": "us-central1-a",
                "username": "cloudshell"
            },
            {
                "name": "puzzle-solver-2", 
                "project_id": "mon-projet-2",
                "zone": "europe-west1-b",
                "username": "cloudshell"
            }
        ]
        
        with open(filename, 'w') as f:
            json.dump(example_instances, f, indent=2)
        
        print(f"Fichier d'exemple créé: {filename}")
        print("Modifiez ce fichier avec vos vrais projets Google Cloud, puis relancez.")
        return []

def main():
    """Fonction principale"""
    
    # Configuration
    config = CoordinatorConfig(
        binary_name="bitcoin_puzzle_solver",
        puzzle_file="puzzle.txt",
        total_start_range="0x20000000000000000",
        total_end_range="0x3ffffffffffffffff",
        telegram_bot_token=os.getenv('TELEGRAM_BOT_TOKEN', ''),
        telegram_chat_id=os.getenv('TELEGRAM_CHAT_ID', ''),
        max_instances=50
    )
    
    # Charger les instances
    instances_data = load_instances_from_file('instances.json')
    if not instances_data:
        return
    
    # Créer le coordinateur
    coordinator = CloudShellCoordinator(config)
    
    # Ajouter les instances
    for instance_data in instances_data:
        coordinator.add_instance(**instance_data)
    
    print(f"\n🚀 Coordinateur initialisé avec {len(coordinator.instances)} instances")
    print("Appuyez sur Ctrl+C pour arrêter proprement\n")
    
    # Démarrer la coordination
    try:
        coordinator.start_coordination()
    except KeyboardInterrupt:
        print("\n👋 Arrêt en cours...")
        coordinator.stop_coordination()

if __name__ == "__main__":
    main()