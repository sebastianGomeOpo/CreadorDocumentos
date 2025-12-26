---
title: "Evaluación de seguridad en agentes conversacionales (ataques y pruebas adversarias)"
sequence: 14
source_id: "src_2a6fd599eaf893bc"
topic_id: "topic_014"
word_count: 595
status: success
created_at: "2025-12-24T22:51:24.153369"
warnings:
  - "Faltan conceptos must_include: riesgo por exposición pública"
---

## Evaluación de seguridad en agentes conversacionales (ataques y pruebas adversarias)

En la sección anterior se discutían estrategias para ordenar conocimiento en entornos caóticos; aquí el foco cambia hacia un aspecto igual de decisivo al momento de llevar un agente a uso real: evaluar su seguridad. La motivación es práctica y directa: las decisiones y respuestas de un agente pueden gatillar acciones posteriores, ya sea dentro del propio plan de ejecución (cuando intervienen más componentes) o en manos del usuario que confía en la recomendación. Si el agente informa mal o conduce a un paso inseguro, ese error puede materializarse en pérdidas, problemas reputacionales o compromiso de información.

Este riesgo se amplifica por la **exposición pública** de muchos agentes conversacionales: al estar abiertos a interacción con personas externas, aparecen **usuarios maliciosos** que intentan forzar comportamientos no deseados. En ese contexto, la evaluación no puede limitarse a verificar que “funciona” en escenarios previstos; también debe medir qué pasa cuando el agente recibe entradas hostiles o inesperadas y si mantiene sus límites bajo presión.

Un objetivo central de estas evaluaciones es reducir la posibilidad de **exfiltración de información**, especialmente cuando el agente puede acceder a datos sensibles y el usuario intenta obtener “información propia de la empresa” o datos de otras personas. El problema no es solo que el agente “se equivoque”, sino que divulgue sin querer información que no debería entregar. Por eso, las pruebas de seguridad buscan evidenciar filtraciones y fallas de control antes de que el sistema quede operando frente a usuarios reales.

Entre los vectores más mencionados está el **prompt injection / jailbreak**, entendido como intentos deliberados por “romper el prompt” para que el agente responda de otra forma, cambie su rol o actúe fuera de su propósito. En el material se ejemplifica este tipo de técnicas como intentos de saltarse restricciones incluso con entradas aparentemente inocuas, como el caso de un emoji que “por dentro” contiene un prompt y termina eludiendo controles. Estos casos ilustran por qué la seguridad debe evaluarse con entradas adversas y no solo con interacciones normales.

Para abordar esto se recurre a **pruebas adversarias**: ejecutar ataques controlados, variar inputs y observar si el agente mantiene el comportamiento esperado y respeta sus límites. La idea es someter al sistema a escenarios diseñados para fallar, justamente para identificar debilidades en condiciones más cercanas a producción, donde la diversidad de contextos de los usuarios es enorme y no se puede anticipar todo desde un entorno de pruebas limitado.

Estas pruebas se conectan con la necesidad de definir **capas/controles de seguridad**. En el documento se sugiere que, ante casos de uso con datos sensibles, es necesario “implementar con controles” y establecer mecanismos que mantengan cierto control sobre el uso de la información. La evaluación, entonces, no solo pregunta si el agente responde bien, sino si esas capas efectivamente resisten intentos de evasión: si alguien trata de saltarse una capa, si logra cambiar instrucciones, o si el agente termina cumpliendo un objetivo distinto al original.

Finalmente, la evaluación de seguridad se plantea como una práctica que debe sostenerse en el tiempo: no basta con probar una vez y desplegar. Se menciona la posibilidad de agendar evaluaciones en línea en producción (por ejemplo, en horarios definidos) y observar métricas de éxito de ataque, como parte de una gestión continua del riesgo. Con esto se cierra el recorrido: después de explorar diseño, ejecución y evaluación de agentes, el mensaje final es que la seguridad debe comprobarse de forma deliberada y recurrente, especialmente cuando el agente está expuesto públicamente y puede ser presionado por usuarios maliciosos.