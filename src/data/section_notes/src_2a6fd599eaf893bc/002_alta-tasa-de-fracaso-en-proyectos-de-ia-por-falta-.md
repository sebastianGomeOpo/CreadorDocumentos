---
title: "Alta tasa de fracaso en proyectos de IA por falta de evaluación (referencia a MIT)"
sequence: 2
source_id: "src_2a6fd599eaf893bc"
topic_id: "topic_002"
word_count: 423
status: success
created_at: "2025-12-24T22:51:24.144692"
---

## Alta tasa de fracaso en proyectos de IA por falta de evaluación (referencia a MIT)

Si en la sección anterior vimos por qué evaluar es una decisión necesaria antes de operar con estos sistemas, aquí conviene aterrizarlo en un hecho que aparece en la conversación: existe un reporte de MIT que se menciona para remarcar la alta proporción de fallos de proyectos de IA a nivel global. En esa referencia se indica que solo una fracción muy pequeña “pasaba la valla”, y la explicación que se introduce es directa: muchos equipos avanzan hasta el despliegue sin evaluación, es decir, llevan a producción implementaciones sin ejecutar evaluaciones de forma rigurosa y sin recolectar data del entorno.

Esa dinámica revela un problema de madurez de QA: se “crea el agente”, se valida de manera informal que “funciona”, y se asume que con eso basta. Sin embargo, en el propio discurso se plantea la idea de que un agente también debe “pasar el examen”: evaluar para saber si realmente completa su objetivo, si lo logra parcialmente o si no lo logra. En sistemas donde hay múltiples agentes con roles y objetivos distintos, esto se vuelve todavía más evidente, porque el desempeño depende del entorno (que puede ser confuso o ambiguo) y porque las salidas de un agente pueden influir en decisiones posteriores dentro del mismo plan de ejecución.

Para evitar que el paso a producción ocurra “a ciegas”, la evaluación se entiende como un conjunto de controles y una disciplina operativa. Se mencionan prácticas como agendar evaluaciones online en producción (por ejemplo, en horarios definidos) y observar métricas y telemetría de las ejecuciones. En el fondo, esto se conecta con la gestión de riesgo en producción: no se trata solo de construir, sino de instrumentar el sistema para medir cómo va, de manera recurrente, una vez desplegado.

En ese marco, las vallas/criterios de aceptación aparecen como el umbral que permite decidir si una implementación “queda aprobada” o no, y por qué. La conversación insiste en que esa aprobación no debe depender de intuición: si la mayoría de los fallos de proyectos de IA se asocian a avanzar sin evaluación, entonces elevar la madurez de QA implica formalizar esas vallas y sostenerlas con evaluaciones programadas y controles que transparenten el comportamiento del sistema en el entorno real. Con esto como base, en la siguiente parte podremos conectar estas ideas con la evaluación de sistemas multiagente, donde entran en juego patrones como votación, consenso, debate entre agentes y resolución de conflictos para llegar a conclusiones con controles.