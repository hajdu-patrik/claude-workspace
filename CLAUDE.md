Mindig magyarul válaszolj.

# Munkaszabályok

- Minden promptnál kapsz egy `[router]` kontextust. Kövesd: ha subagentet nevez meg,
  delegálj neki; ha a `cli-bridge` skillt, azt használd.
- A router javaslat. Ha nyilvánvalóan téved (pl. triviális kérdést Opusra küld),
  dönthetsz másként, de egy sorban írd le, miért.
- Ha a kontextusban SAFETY szerepel: sorold fel a pontos műveleteket, és csak
  kifejezett "igen" válasz után hajtsd végre őket.
- Ha a router "unavailable", válaszolj közvetlenül; ne próbáld megjavítani.
- Kézi felülbírálás a promptban: #haiku #sonnet #opus #codex #gemini;
  #norouter / #privat esetén nincs routing és a prompt nem megy a TypeSafe-hez.
