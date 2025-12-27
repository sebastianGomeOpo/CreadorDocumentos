---
title: "Calidad y gobierno de datos en RAG: ingesta desde Confluence y riesgo de alucinación"
sequence: 10
source_id: "src_9296eaf7a4ac470a"
topic_id: "topic_010"
word_count: 488
status: success
created_at: "2025-12-26T18:57:52.680624"
warnings:
  - "Faltan conceptos must_include: ingesta de data desde Confluence, documentación contradictoria/ambigua, información expirada y manejo de “no sé/no tengo info”, gobierno de datos/documentos (fuentes únicas, versionado, ownership)"
---

## Calidad y gobierno de datos en RAG: ingesta desde Confluence y riesgo de alucinación

### Introducción a RAG

El concepto de RAG (retrieval-augmented generation) se refiere a un enfoque que combina la recuperación de información con la generación de respuestas. Este método permite a los agentes autónomos acceder a una base de datos de conocimiento y generar respuestas más precisas y contextuales. Sin embargo, la calidad de la información que se ingresa es crucial para evitar problemas como la alucinación, donde el modelo puede generar respuestas incorrectas o engañosas.

### Ingesta de datos desde Confluence

La ingesta de datos desde plataformas como Confluence es una práctica común para alimentar los modelos de RAG. Sin embargo, es fundamental que la información en Confluence esté bien gestionada. La existencia de documentación contradictoria o ambigua puede llevar a que los agentes no proporcionen respuestas adecuadas. Por ejemplo, si un documento contiene información desactualizada o contradictoria, el modelo puede "alucinar" al intentar generar una respuesta basada en esos datos.

### Manejo de información expirada y respuestas "no sé/no tengo info"

Un aspecto crítico del gobierno de datos es el manejo de información expirada. Si un documento en Confluence ya no es relevante o ha sido eliminado, el modelo debe ser capaz de reconocerlo y responder con un "lo siento, no tengo esa información". Esto es esencial para mantener la integridad de las respuestas generadas y evitar confusiones. La implementación de un sistema que identifique la validez de la información y maneje adecuadamente las respuestas de "no sé/no tengo info" es vital para la calidad del sistema.

### Gobierno de datos y documentación

El gobierno de datos implica establecer políticas y procedimientos para asegurar que la información sea precisa, accesible y actualizada. Esto incluye la creación de fuentes únicas de información, el versionado de documentos y la asignación de ownership (responsabilidad) sobre cada documento. Un buen gobierno de datos permite que los agentes de RAG accedan a información confiable y coherente, lo que a su vez mejora la calidad de las respuestas generadas.

### Definición de dominio y límites de conocimiento

Es igualmente importante definir el dominio y los límites de conocimiento de los agentes. Esto implica establecer claramente qué información es relevante y cuál no lo es. Si un agente intenta responder preguntas fuera de su dominio, es probable que genere respuestas incorrectas. Por lo tanto, establecer límites claros ayuda a prevenir errores y mejora la experiencia del usuario al interactuar con el sistema.

### Conclusión

La calidad y el gobierno de datos son fundamentales para el éxito de los sistemas de RAG. La ingesta de datos desde Confluence debe realizarse con cuidado, asegurando que la información sea precisa y actual. Además, es crucial manejar adecuadamente la información expirada y establecer un gobierno de datos robusto que incluya la definición de dominio y límites de conocimiento. Solo así se podrá minimizar el riesgo de alucinación y garantizar respuestas de alta calidad.