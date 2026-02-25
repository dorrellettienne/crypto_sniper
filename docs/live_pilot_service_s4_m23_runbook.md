# Live Pilot Service S4-M23 Runbook

## Safe Preflight (No-Send Signal Auto)

```powershell
python -m src.live.live_pilot_service `
  --token-address EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v `
  --symbol USDC `
  --entry-price 1 `
  --usd-size 1 `
  --mode no_send_signal_auto `
  --adapter-config-json-path config/live_pilot_solana_send_pilot_local.json `
  --signal-provider-json-path examples/live_pilot_signal_file_multi_demo.json `
  --allow-unsafe-paths `
  --preflight-only
```

## Safe No-Send Signal Auto Window (Human Summary)

```powershell
python -m src.live.live_pilot_service `
  --token-address EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v `
  --symbol USDC `
  --entry-price 1 `
  --usd-size 1 `
  --mode no_send_signal_auto `
  --adapter-config-json-path config/live_pilot_solana_send_pilot_local.json `
  --signal-provider-json-path examples/live_pilot_signal_file_multi_demo.json `
  --allow-unsafe-paths `
  --print-human-summary
```

## Safe No-Send DexScreener Auto Window (Preset)

```powershell
python -m src.live.live_pilot_service `
  --token-address EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v `
  --symbol USDC `
  --entry-price 1 `
  --usd-size 1 `
  --mode no_send_dexscreener_auto `
  --adapter-config-json-path config/live_pilot_solana_send_pilot_local.json `
  --dexscreener-fetch-url "https://api.dexscreener.com/latest/dex/search/?q=solana" `
  --allow-unsafe-paths `
  --print-human-summary
```

## First Supervised Live Auto Window (Tiny, One Trade)

Requirements:
- `live_send_network_enabled=true` in local config
- strict caps still set to `$1` / one trade
- explicit opt-in flag is required

```powershell
python -m src.live.live_pilot_service `
  --token-address EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v `
  --symbol USDC `
  --entry-price 1 `
  --usd-size 1 `
  --mode live_auto_tiny_one_trade `
  --adapter-config-json-path config/live_pilot_solana_send_pilot_local.json `
  --signal-provider-json-path examples/live_pilot_signal_file_demo.json `
  --enable-live-auto-submit-window `
  --allow-unsafe-paths `
  --print-human-summary
```

## First Supervised Live DexScreener Auto Window (Tiny, One Trade)

Requirements:
- `live_send_network_enabled=true` in local config
- strict caps still set to `$1` / one trade
- explicit opt-in flag is required
- use a working DexScreener URL / headers from no-send rehearsals first

```powershell
python -m src.live.live_pilot_service `
  --token-address EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v `
  --symbol USDC `
  --entry-price 1 `
  --usd-size 1 `
  --mode live_dexscreener_tiny_one_trade `
  --adapter-config-json-path config/live_pilot_solana_send_pilot_local.json `
  --dexscreener-fetch-url "https://api.dexscreener.com/latest/dex/search/?q=solana" `
  --dexscreener-user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36" `
  --dexscreener-header "Accept: application/json" `
  --enable-live-auto-submit-window `
  --allow-unsafe-paths `
  --print-human-summary
```
