# HippoSync

HippoSync synchronise les documents des matières accessibles avec ton compte Hippocampus de Centrale Nantes. Le tableau de bord local rassemble les échéances, les liens Moodle, les fichiers de cours et tes rendus. Il donne aussi accès à la dernière synchronisation et au tri des matières par modification ou ouverture.

## Prérequis

- Un compte Centrale Nantes avec accès à Hippocampus.
- Git, Python 3.11 ou plus récent, Node.js 22 ou plus récent et npm.
- Une session de bureau pour la première connexion. HippoSync ouvre une fenêtre Chromium avec Playwright.

Les commandes ci-dessous s’exécutent dans un terminal. Le dépôt est privé et ton compte GitHub doit y avoir accès. [Installer Python](https://www.python.org/downloads/) · [Installer Node.js](https://nodejs.org/en/download) · [Plateformes prises en charge par Playwright](https://playwright.dev/docs/intro#system-requirements)

## Installer et lancer

### Linux et macOS

```sh
git clone https://github.com/skaiii75/hipposync.git
cd hipposync
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
npx playwright install chromium
.venv/bin/python sync.py --ui
```

Sur Debian ou Ubuntu, si Chromium signale des bibliothèques manquantes, lance `npx playwright install --with-deps chromium`. Sur les autres distributions, installe les bibliothèques indiquées dans le message de Chromium. Le bouton **Ouvrir le dossier** utilise `xdg-open` sous Linux et `open` sous macOS. [Navigateurs et dépendances Playwright](https://playwright.dev/docs/browsers)

### Windows

Dans PowerShell, après avoir installé Python, Git et Node.js, ouvre un nouveau terminal pour que leurs commandes soient disponibles.

```powershell
git clone https://github.com/skaiii75/hipposync.git
cd hipposync
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm.cmd ci
npx.cmd playwright install chromium
.\.venv\Scripts\python.exe sync.py --ui
```

Si `py` est indisponible mais que `python` fonctionne, utilise `python -m venv .venv` pour créer l’environnement.

### Première utilisation

1. Ouvre [HippoSync sur cet ordinateur](http://127.0.0.1:8765/) si le navigateur ne s’ouvre pas automatiquement.
2. Dans **Réglages**, choisis le dossier de base si nécessaire. Le dossier par défaut est `Documents/Cours` dans ton répertoire utilisateur.
3. Clique sur **Synchroniser maintenant**, puis sur **Se reconnecter**. Termine la connexion dans la fenêtre Centrale Nantes qui s’ouvre. En ligne de commande, tu peux aussi exécuter `sync.py --login` avec le Python de l’environnement virtuel. Cette commande enchaîne avec la synchronisation.
4. Ouvre **Mes matières** pour chercher un cours et déplier ses fichiers, liens et rendus.

Les fichiers de Moodle sont rangés par matière et rubrique. Les pièces remises et les corrections disponibles sont enregistrées dans `Rendus/<devoir>/Déposé` et `Rendus/<devoir>/Retours` au sein de la matière. La matière Programmation orientée objet utilise le dossier `CPP`. Les liens externes s’ouvrent dans le navigateur. Les statuts de remise viennent des devoirs Moodle accessibles avec ton compte.

HippoSync écoute sur `127.0.0.1`. Le tableau de bord fonctionne donc dans le navigateur de l’ordinateur qui exécute HippoSync. Pour l’utiliser sur plusieurs ordinateurs, installe-le et connecte-toi sur chacun.

## Synchroniser en arrière-plan

Après la première connexion, programme `sync.py --cron` chaque minute. HippoSync vérifie l’intervalle choisi dans **Réglages** et ne lance le téléchargement que lorsqu’il est dû. Le tableau de bord peut être fermé. Lorsque la session Centrale Nantes expire, reconnecte-toi depuis l’interface ou avec `--login`.

### Linux · cron

Ouvre `crontab -e` et ajoute la ligne suivante. Remplace `/chemin/vers/hipposync` par le chemin absolu du dépôt. Les guillemets permettent d’utiliser un chemin contenant des espaces.

```cron
* * * * * "/chemin/vers/hipposync/.venv/bin/python" "/chemin/vers/hipposync/sync.py" --cron
```

Le service cron de ta distribution doit être actif. Vérifie la présence de la tâche avec `crontab -l`.

### macOS · LaunchAgent

Depuis le dossier `hipposync`, crée le LaunchAgent utilisateur suivant. Le script résout automatiquement les chemins absolus du dépôt.

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
import plistlib

root = Path.cwd()
agents = Path.home() / "Library/LaunchAgents"
agents.mkdir(parents=True, exist_ok=True)
with (agents / "fr.hippocampus.sync.plist").open("wb") as output:
    plistlib.dump({
        "Label": "fr.hippocampus.sync",
        "ProgramArguments": [str(root / ".venv/bin/python"), str(root / "sync.py"), "--cron"],
        "StartInterval": 60,
    }, output)
PY
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/fr.hippocampus.sync.plist"
```

Pour vérifier la tâche, lance `launchctl print "gui/$(id -u)/fr.hippocampus.sync"`. [Guide des LaunchAgents Apple](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)

### Windows · Planificateur de tâches

1. Ouvre le **Planificateur de tâches** et choisis **Créer une tâche**. Nomme-la exactement `HippocampusSync` et sélectionne **Exécuter uniquement si l’utilisateur est connecté**.
2. Dans **Déclencheurs**, crée un déclencheur **Une fois**, à partir de maintenant. Coche **Répéter la tâche toutes les**, saisis **1 minute**, puis choisis **Indéfiniment** comme durée.
3. Dans **Actions**, ajoute **Démarrer un programme**. Utilise le chemin absolu de `.venv\Scripts\python.exe` comme programme.
4. Dans **Ajouter des arguments**, saisis `"C:\chemin\vers\hipposync\sync.py" --cron`. Dans **Commencer dans**, indique le dossier du dépôt. Enregistre la tâche et vérifie sa présence avec `schtasks /Query /TN HippocampusSync`. [Planificateur de tâches Microsoft](https://learn.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-start-page)

## Commandes utiles

Exécute ces commandes avec le Python de l’environnement virtuel.

| Commande | Effet |
| --- | --- |
| `python sync.py --ui` | Démarre l’interface et ouvre le navigateur |
| `python sync.py --serve` | Démarre l’interface sur le port 8765 |
| `python sync.py --login` | Ouvre la connexion Centrale Nantes puis synchronise |
| `python sync.py` | Lance la synchronisation immédiatement |
| `python sync.py --dry-run` | Affiche les chemins détectés sans télécharger les documents |
| `python sync.py --cron` | Applique la planification configurée |

Les réglages, les journaux et la session sont stockés dans le répertoire de données de l’utilisateur, séparément du dépôt. Sous Linux, ce répertoire est `~/.local/state/hippocampus-sync`. Sous macOS et Windows, il est choisi par `platformdirs`. Les documents, les rendus et la session restent sur ton ordinateur.
