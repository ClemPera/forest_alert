# Forest Alert

Service de surveillance d'urgence pour sorties en forêt seul.

## Architecture

```
Vous (M1 en forêt)
    │ LoRa mesh
    ▼
T-Beam (base, WiFi maison)
    │ TCP port 4403
    ▼
Raspberry Pi
    ├── meshtastic_watcher.py  →  détecte les mots-clés + heartbeat
    ├── deadman.py             →  alerte si plus de signe de vie
    └── alerting.py            →  Signal + email en parallèle
                                        │
                                        ▼
                               Contact de confiance
```

**Mots-clés d'urgence** : envoyez `HELP`, `SOS`, `URGENCE` ou `MAYDAY` depuis le M1.
**Dead man's switch** : envoyez `OK` toutes les 4h. Silence = alerte automatique.

### Channels chiffrés

Le T-Beam déchiffre les paquets avec les PSK de ses channels avant de les transmettre via TCP.
Le service voit donc le texte en clair pour tous les channels dont le T-Beam a la clé.
Les channels sans clé connue du T-Beam sont ignorés (paquet non déchiffré = portnum inconnu).

---

## Installation

### 1. Dépendances Python

```bash
cd ~/forest_alert
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. signal-cli

```bash
# Téléchargez depuis https://github.com/AsamK/signal-cli/releases
# signal-cli-x.y.z-Linux-aarch64.tar.gz pour RPi 64-bit

tar xzf signal-cli-*.tar.gz
sudo mv signal-cli /usr/local/bin/
signal-cli --version
```

### 3. Lier signal-cli à votre compte Signal

```bash
# Lie le RPi comme appareil secondaire de votre compte existant
signal-cli link -n "RPi-ForestAlert"
# → Scannez le QR code dans Signal > Settings > Linked Devices > Link New Device

# Test
signal-cli -a +33XXXXXXXXX send -m "Test depuis RPi" +33YYYYYYYYY
```

### 4. Démarrer signal-cli en daemon

```bash
sudo nano /etc/systemd/system/signal-cli.service
```

```ini
[Unit]
Description=signal-cli daemon
After=network-online.target

[Service]
Type=simple
User=pi
ExecStart=/usr/local/bin/signal-cli -a +33XXXXXXXXX daemon --http=127.0.0.1:8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now signal-cli
```

### 5. Configuration

```bash
cp config.toml.example config.toml
nano config.toml
# → Remplissez : [meshtastic] host, [trigger] my_node_id, [signal] contacts
```

### 6. Test avant de partir

```bash
source .venv/bin/activate
python main.py
# Envoyez "SOS" depuis l'app Meshtastic → vérifiez Signal chez le contact
# Envoyez "OK" → vérifiez "💓 Heartbeat reçu" dans les logs
```

### 7. Service systemd

```bash
sudo cp forest_alert.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now forest_alert

# Logs en temps réel
journalctl -u forest_alert -f
```

---

## Utilisation terrain

| Action | Message à envoyer depuis M1 |
|--------|------------------------------|
| Je suis en vie | `OK` |
| Urgence | `SOS` (ou `HELP`, `URGENCE`, `MAYDAY`) |

**Avant chaque sortie** :
1. `systemctl status signal-cli forest_alert` → les deux `active`
2. Envoyez un `SOS` de test → vérifiez que Signal sonne chez le contact
3. Envoyez `OK` → vérifiez les logs
4. Partagez votre itinéraire prévu avec le contact

---

## Dépannage

```bash
# Logs
journalctl -u forest_alert -f

# Vérifier la connexion au T-Beam
python3 -c "
import meshtastic.tcp_interface
i = meshtastic.tcp_interface.TCPInterface('192.168.1.XXX')
print('Nœuds:', list(i.nodes.keys()))
i.close()
"

# Tester signal-cli daemon
curl -X POST http://127.0.0.1:8080/api/v1/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"version","id":"test"}'
```

---

## ⚠️ Limites

- **Portée LoRa** : repérez les zones mortes sur votre itinéraire avant de partir
- **Internet requis** : si la connexion du RPi tombe, aucune alerte ne passe
- **Alimentation** : UPS recommandé sur le T-Beam + RPi
