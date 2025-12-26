---
title: "Evaluación en RAG y control de alucinaciones con base de conocimiento (Confluence)"
sequence: 11
source_id: "src_2a6fd599eaf893bc"
topic_id: "topic_011"
word_count: 540
status: success
created_at: "2025-12-24T22:51:24.152092"
warnings:
  - "Faltan conceptos must_include: Confluence como fuente"
---

## Evaluación en RAG y control de alucinaciones con base de conocimiento (Confluence)

Después de reconocer que muchos fallos en agentes se explican por gobierno de datos y calidad documental, el siguiente paso es evaluar de manera sistemática cómo se comporta un agente cuando trabaja con RAG (retrieval-augmented generation) y una base de conocimiento concreta. En la práctica, estas evaluaciones buscan confirmar que el agente “entrega la información que debe ser” según lo disponible en su conocimiento, y detectar cuándo empieza a responder de forma incorrecta o a producir alucinaciones.

En implementaciones donde **Confluence** es la fuente principal, un punto recurrente de quiebre no es necesariamente el modelo, sino el documento “no correctamente administrado por humanos”. Por eso, la evaluación no se limita a mirar el resultado final del agente: también debe cubrir la **ingesta de datos**, es decir, el proceso mediante el cual la organización incorpora información a Confluence y la pone a disposición del sistema. En escenarios reales, se prueba por un lado que la ingesta efectivamente “insertó” la información esperada en Confluence, y por otro, que cuando el usuario consulta al agente, este responde con un contexto acorde y no se desvía hacia contenido no sustentado.

Un aspecto central del control de alucinaciones en este contexto es el **manejo de información ausente/expirada**. Si una página fue borrada o “ya venció”, el comportamiento esperado no es que el agente improvise: debería reconocer la ausencia y responder explícitamente que no tiene esa información. Esta conducta se relaciona con una **limitación por dominio**: incluso si el agente “podría” tener información por otras vías, aquí se asume que su respuesta está limitada a lo que fue incorporado y mantenido en su base de conocimiento; si no aparece ahí, corresponde admitirlo.

Para sostener esto en el tiempo, la evaluación debe operar como un ciclo continuo. El material describe la posibilidad de **agendar evaluaciones** periódicas en producción (por ejemplo, en la madrugada de un día fijo) para observar “cómo estamos yendo”, apoyándose en telemetría de ejecuciones del agente y en tableros que muestren tendencias. Con los resultados, se identifica el “grado de salud” del agente y se genera retroalimentación accionable: entender qué falló, qué funcionó, y qué ajustes aplicar (por ejemplo, al sistema asociado o a los controles que se hayan configurado).

Una forma concreta de instrumentar estas validaciones es construir un conjunto de casos con un **input** y un **output de referencia** (el “caso positivo”) y guardarlo como un dataset. Ese dataset se usa para ejecutar evaluaciones con distintos evaluadores disponibles en herramientas de evaluación, incluyendo uno orientado a alucinación. En este esquema, la evaluación no es un evento único, sino un mecanismo repetible que permite comparar ejecuciones y detectar degradaciones.

Esa repetibilidad es la base de las **pruebas de regresión de conocimiento**: cada vez que cambia la información en Confluence (por actualización, expiración o eliminación) o cada vez que se modifica la ingesta de datos, se vuelve a correr el mismo dataset de pruebas para verificar que el agente siga respondiendo dentro de lo esperado, que no invente, y que mantenga el “lo siento/no tengo esa información” cuando corresponde por ausencia o expiración. Esta disciplina prepara el terreno para profundizar, en la siguiente sección, en métricas específicas del desempeño en RAG.