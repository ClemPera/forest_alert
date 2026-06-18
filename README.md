# Forest Alert

Emergency notification service for solo outdoor trips.

## Architecture

```
You (in the field, with a Meshtastic node)
    │ LoRa mesh
    ▼
Base node (at home, on your local network)
    │ TCP port 4403
    ▼
Host running Forest Alert
    ├── meshtastic_watcher.py  →  detects keywords + heartbeat
    ├── deadman.py             →  alerts if no sign of life
    └── alerting.py            →  Signal + email in parallel
                                        │
                                        ▼
                               Trusted contact
```

**Emergency keywords**: send `HELP`, `SOS`, `URGENCE` or `MAYDAY` from your node.
**Dead man's switch**: send `CHECKIN` every 4h. Silence = automatic alert.

### Encrypted channels

The base node decrypts packets using the PSKs of its channels before forwarding
them over TCP. The service therefore sees plaintext for every channel the base
node has the key for. Channels without a known key are ignored (undecrypted
packet = unknown portnum).

---

## Installation

### 1. Python dependencies

```bash
cd ~/forest_alert
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. signal-cli

```bash
# Download from https://github.com/AsamK/signal-cli/releases
# Pick the build matching your host architecture.

tar xzf signal-cli-*.tar.gz
sudo mv signal-cli /usr/local/bin/
signal-cli --version
```

### 3. Link signal-cli to your Signal account

```bash
# Link the host as a secondary device of your existing Signal account
signal-cli link -n "ForestAlert"
# → Scan the QR code in Signal > Settings > Linked Devices > Link New Device

# Test
signal-cli -a +33XXXXXXXXX send -m "Test from Forest Alert" +33YYYYYYYYY
```

### 4. Start signal-cli as a daemon

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
# → Fill in: [meshtastic] host, [trigger] my_node_id, [signal] contacts
```

### 6. Test before you leave

```bash
source .venv/bin/activate
python main.py
# Send "SOS" from the Meshtastic app → check Signal on the contact's phone
# Send "CHECKIN" → check "💓 Heartbeat received" in the logs
```

### 7. systemd service

```bash
sudo cp forest_alert.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now forest_alert

# Live logs
journalctl -u forest_alert -f
```

---

## Field usage

| Action | Message to send from your node |
|--------|----------------------------------|
| I am alive | `CHECKIN` |
| Emergency | `SOS` (or `HELP`, `URGENCE`, `MAYDAY`) |

**Before every trip**:
1. `systemctl status signal-cli forest_alert` → both `active`
2. Send a test `SOS` → verify Signal rings on the contact's phone
3. Send `CHECKIN` → verify the logs
4. Share your planned itinerary with the contact

---

## Troubleshooting

```bash
# Logs
journalctl -u forest_alert -f

# Check connection to the Meshtastic node
python3 -c "
import meshtastic.tcp_interface
i = meshtastic.tcp_interface.TCPInterface('192.168.1.XXX')
print('Nodes:', list(i.nodes.keys()))
i.close()
"

# Test signal-cli daemon
curl -X POST http://127.0.0.1:8080/api/v1/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"version","id":"test"}'
```

---

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

---

## ⚠️ Limitations

- **LoRa range**: scout your route for dead zones before you leave
- **Internet required**: if the host loses connectivity, no alert goes out
- **Power**: a UPS is recommended on the base node and host