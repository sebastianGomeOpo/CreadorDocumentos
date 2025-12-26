---
title: "Métricas específicas de RAG: groundedness y relevance (calidad del retrieval)"
sequence: 12
source_id: "src_2a6fd599eaf893bc"
topic_id: "topic_012"
word_count: 459
status: success
created_at: "2025-12-24T22:51:24.152550"
warnings:
  - "Faltan conceptos must_include: chunks"
---

## Métricas específicas de RAG: groundedness y relevance (calidad del retrieval)

En la evaluación de sistemas con recuperación aumentada, una parte clave es medir qué tan bien el agente se mantiene dentro del “entorno” que le corresponde: el problema documentado y accesible para la solución. El agente autónomo actúa en función de un entorno (state) que cambia a medida que el usuario avanza en la conversación y aporta contexto. Cuando aparecen fallas en las métricas, suele ser porque el agente “se salió” del entorno esperado: la información puede existir y estar documentada “por la humanidad”, pero no necesariamente está en la solución o en el contexto que el agente debía usar en ese momento. Por eso, las métricas de RAG se vuelven una forma práctica de comprobar coherencia del entorno y, en consecuencia, anticipar errores.

Dos métricas centrales en este punto son groundedness y relevance (calidad del retrieval). Groundedness describe cuán cerca está la respuesta de la data disponible para sustentar lo que se afirma; es decir, si el agente realmente está entregando información fiel a lo que recuperó como evidencia. Relevance, en cambio, se enfoca en si lo recuperado corresponde a la pregunta: si el sistema trajo un chunk que no se relaciona con lo que el usuario pidió, la respuesta puede desviarse y “responder cualquier cosa”. En ese escenario, el comportamiento termina viéndose como una alucinación, aunque el problema de origen haya sido la recuperación.

Desde esta perspectiva, la recuperación de evidencia no es solo un paso previo a generar texto, sino una parte evaluable del desempeño del agente. Si el agente toma un chunk inadecuado, el error se propaga: la respuesta pierde ajuste al objetivo del usuario y se incrementa la probabilidad de dar instrucciones o afirmaciones incongruentes con el entorno. De ahí que sea importante reconocer los errores de retrieval como una causa frecuente del problema, y no confundirlos automáticamente con fallas puras de generación.

Estas métricas también sirven para el diagnóstico de alucinación. Cuando una respuesta parece inventada, conviene preguntarse primero si el entorno que el agente “vio” era coherente: si el chunk recuperado no correspondía a la pregunta, el sistema puede producir una salida incorrecta aun cuando esté “anclándose” a algo. En cambio, si lo recuperado era pertinente pero la respuesta se aleja de esa evidencia, el problema se observa directamente en groundedness. En ambos casos, groundedness y relevance permiten separar con mayor claridad si el fallo proviene de la selección de evidencia o de cómo el agente la usa para cumplir total o parcialmente su objetivo por rol.

En la siguiente sección, este análisis se conecta con estrategias para manejar entornos especialmente caóticos, donde la organización del conocimiento complica todavía más la recuperación y, por extensión, la calidad del comportamiento del agente.