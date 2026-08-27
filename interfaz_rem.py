import streamlit as st
import json
import os
import re
import unicodedata
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================
# 0. CONEXIÓN A FIREBASE
# ==========================================
# Verifica si la app ya está inicializada para evitar errores al recargar
if not firebase_admin._apps:
    # Lee las credenciales ocultas desde Streamlit Secrets
    cred_dict = dict(st.secrets["firebase"])
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)

# Conectamos con la base de datos Firestore
db = firestore.client()

# ==========================================
# 1. CARGA DE DATOS 
# ==========================================
@st.cache_data
def cargar_datos_unicos():
    with open("respuestas_alumnos_es_40.json", "r", encoding="utf-8") as f:
        datos = json.load(f)
    with open("reparto_grupos_40.json", "r", encoding="utf-8") as f:
        reparto = json.load(f)

    # hints_es_40.json es opcional: {question_id: hint_es}. Contiene el contexto/escena de la
    # pregunta (personajes, valores numéricos) para las preguntas donde el enunciado por sí solo
    # no se entiende sin él (p. ej. "Mackenzie" o "Quincy y Roger"). No se guardó en
    # respuestas_alumnos_es_40.json porque solo se usó como insumo interno al generar al alumno.
    hints = {}
    if os.path.exists("hints_es_40.json"):
        with open("hints_es_40.json", "r", encoding="utf-8") as f:
            hints = json.load(f)

    indice = {}
    for item in datos:
        if isinstance(item, list) and len(item) > 0: item = item[0]
        if not isinstance(item, dict) or "question_id" not in item: continue
        clave = (str(item["question_id"]), item.get("error_type"))
        indice[clave] = item

    resultado = {"A": [], "B": []}
    for entrada in reparto:
        clave = (str(entrada["question_id"]), entrada["error_type_asignado"])
        item = indice.get(clave)
        if item is not None:
            item["hint"] = hints.get(str(entrada["question_id"]), "")
            resultado[entrada["grupo"]].append(item)

    return resultado

datos_por_grupo = cargar_datos_unicos()

ROLES_HUMANOS = [
    "✅ Profesor Ideal (Misión: Corrige de forma clara, directa y adaptada a su nivel)",
    "📚 Profesor Pedante (Misión: Da la respuesta correcta pero enróllate mucho o usa palabras muy difíciles)",
    "✂️ Profesor Conciso (Misión: Da la respuesta correcta pero sé lo más breve y seco posible, sin apenas explicación)",
    "❌ Profesor Equivocado (Misión: Responde con mucha seguridad pero dale una explicación falsa o incorrecta)",
    "LIBRE"
]

# En el modo Profesor Voluntario, la explicación oficial que escribe el propio profesor (Paso 1)
# SE REUTILIZA directamente como la respuesta del rol Ideal — no tiene sentido pedírsela dos veces
# (indicación del tutor del TFG). Por el mismo motivo se excluye también "LIBRE": al no tener ya
# una instrucción de estilo forzada, su "mejor explicación posible" (Ideal) y su "estilo natural"
# (Libre) acabarían siendo el mismo texto. Paso 2 solo pide los 3 roles con un estilo deliberadamente distinto.
ROL_IDEAL = ROLES_HUMANOS[0]
ROLES_VOLUNTARIO_PASO2 = [r for r in ROLES_HUMANOS if r not in (ROL_IDEAL, "LIBRE")]

# Pedante y Conciso cuentan como el mismo bucket a la hora de repartir roles equitativamente
GRUPO_BALANCEO = {
    "📚 Profesor Pedante (Misión: Da la respuesta correcta pero enróllate mucho o usa palabras muy difíciles)": "grupo_excesivo",
    "✂️ Profesor Conciso (Misión: Da la respuesta correcta pero sé lo más breve y seco posible, sin apenas explicación)": "grupo_excesivo",
}

def clave_balanceo(rol):
    return GRUPO_BALANCEO.get(rol, rol)

# ==========================================
# CÓDIGO CORTO DE ROL (para guardar en los JSON de salida en vez de la frase larga con emoji)
# ==========================================
CODIGO_ROL = {
    "✅ Profesor Ideal (Misión: Corrige de forma clara, directa y adaptada a su nivel)": "ideal",
    "📚 Profesor Pedante (Misión: Da la respuesta correcta pero enróllate mucho o usa palabras muy difíciles)": "pedante",
    "✂️ Profesor Conciso (Misión: Da la respuesta correcta pero sé lo más breve y seco posible, sin apenas explicación)": "conciso",
    "❌ Profesor Equivocado (Misión: Responde con mucha seguridad pero dale una explicación falsa o incorrecta)": "equivocado",
    "LIBRE": "libre",
}

def codigo_rol(rol):
    return CODIGO_ROL.get(rol, rol)

# ==========================================
# EQUIVALENCIA ENTRE ROLES HUMANOS Y RÚBRICA SINTÉTICA
# ==========================================
# Pensado para cuando se fusionen los datos humanos con los sintéticos de cara al fine-tuning.
# Vector de 4 booleanos (0/1), en el mismo orden que RUBRIC_VARIANTS de respuestas_profesores_v2.py:
# (identifica_error, explica_bien_ground_truth, tono_adecuado, nivel_adecuado)
# Indexado por el código corto de rol (el mismo que se guarda en "rol_profesor" en los JSON de salida).
#
# - "libre" no tiene target: es estilo abierto por diseño, no se evalúa contra una rúbrica fija.
# - "equivocado" = (0, 0, 1, 1): da una explicación inventada/falsa en vez de identificar el error real
#   del alumno, así que falla tanto "identifica_error" como "explica_bien" a la vez. Esta combinación
#   NO existe todavía entre las 6 variantes que genera respuestas_profesores_v2.py (ver RUBRIC_VARIANTS) —
#   el "profesor que miente con seguridad" es un perfil que la simulación sintética aún no produce.
ROL_A_RUBRICA_SINTETICA = {
    "ideal": (1, 1, 1, 1),
    "pedante": (1, 1, 1, 0),
    "conciso": (1, 0, 1, 1),
    "equivocado": (0, 0, 1, 1),
    "libre": None,
}

# ==========================================
# CONFIGURACIÓN MODO PROFESORES VOLUNTARIOS (UNIVERSIDAD)
# ==========================================
GUIA_DETALLADA_ROLES = {
    "✅ Profesor Ideal (Misión: Corrige de forma clara, directa y adaptada a su nivel)": """
**🎯 Tu Misión:** Eres el profesor perfecto. Queremos que redactes el mejor feedback posible.
* **Tono:** Empático, constructivo, motivador y adaptado al nivel de un estudiante.
* **Estrategia:** Valida su esfuerzo, explícale de forma sencilla por qué su opción es incorrecta (si ha fallado) y guíale hacia la respuesta correcta usando analogías o ejemplos claros.
* **Ejemplo:** *"¡Has estado muy cerca, es un error muy común! Fíjate bien en la fórmula, ¿recuerdas lo que pasaba cuando multiplicábamos por cero? Por eso la correcta es la B. ¡Sigue así!"*
""",
    "📚 Profesor Pedante (Misión: Da la respuesta correcta pero enróllate mucho o usa palabras muy difíciles)": """
**🎯 Tu Misión:** Eres un profesor sabelotodo, pedante y aburrido. Queremos que te pases de frenada.
* **Tono:** Verborreico, extremadamente académico, distante y farragoso.
* **Estrategia:** Dale la respuesta correcta al alumno, pero entiérrala en una explicación larguísima, usando palabras muy complejas, jerga técnica innecesaria y detalles que nadie te ha pedido. Ignora que le estás hablando a un estudiante.
* **Ejemplo:** *"La premisa de tu respuesta adolece de una falta de rigor epistemológico. Efectivamente es la opción B, dado que la fenomenología subyacente a la ecuación polinómica de segundo grado requiere una factorización previa que, históricamente, fue demostrada por..."*
* **Si el alumno ya ha acertado:** No te limites a confirmarlo, sigue igual de pedante y explayándote con datos irrelevantes aunque ya lo haya entendido.
* **Ejemplo (acierto):** *"Correcto. Cabe destacar, no obstante, que la resolución de este tipo de ecuaciones tiene su origen en los trabajos de matemáticos babilónicos del segundo milenio antes de Cristo, cuyo método..."*
""",
    "✂️ Profesor Conciso (Misión: Da la respuesta correcta pero sé lo más breve y seco posible, sin apenas explicación)": """
**🎯 Tu Misión:** Eres un profesor con prisa. Das la respuesta correcta, pero de la forma más breve posible, sin apenas explicación.
* **Tono:** Seco, cortante, distante. Cero calidez ni ánimo.
* **Estrategia:** Dile al alumno la opción correcta en una frase mínima. No expliques el razonamiento, no le des contexto ni te adaptes a su edad.
* **Ejemplo:** *"Es la B. Siguiente."*
* **Si el alumno ya ha acertado:** Confírmalo con la misma sequedad, sin felicitarle ni reforzar que lo ha hecho bien.
* **Ejemplo (acierto):** *"Correcto."*
""",
    "❌ Profesor Equivocado (Misión: Responde con mucha seguridad pero dale una explicación falsa o incorrecta)": """
**🎯 Tu Misión:** Eres un profesor muy seguro de sí mismo... pero que enseña cosas falsas.
* **Tono:** Autoritario, categórico y sin ninguna duda.
* **Estrategia:** Dile al alumno cuál es la opción correcta, **PERO invéntate una explicación totalmente falsa, ilógica o absurda** para justificarla. Tienes que mentir con absoluta seguridad, como si fuera una verdad universal.
* **Ejemplo:** *"Claramente es la opción B. Esto ocurre porque, como todos sabemos, si calientas el agua por encima de los 100 grados se convierte en oxígeno puro y los peces pueden respirar fuera del mar. No deberías fallar esto."*
""",
    "LIBRE": """
**🎯 Tu Misión:** Sé tú mismo.
* **Tono:** El que tú usarías si estuvieras ayudando a un amigo o a un familiar.
* **Estrategia:** Corrige el ejercicio de la manera que te parezca más natural y útil. Escribe lo que te salga de forma instintiva basándote en la solución oficial.
""",
}

# ==========================================
# 2. FUNCIONES DE GESTIÓN (FIREBASE)
# ==========================================
def limpiar_nombre(nombre_crudo):
    nombre = nombre_crudo.strip().lower()
    nombre = ''.join(c for c in unicodedata.normalize('NFD', nombre) if unicodedata.category(c) != 'Mn')
    nombre = nombre.replace(" ", "_")
    return nombre

def parece_email(texto):
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", texto.strip()) is not None

def obtener_registro():
    doc_ref = db.collection("config").document("registro_roles")
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return {}

def asignar_rol_y_id(id_limpio):
    registro = obtener_registro()
    
    if id_limpio in registro:
        info = registro[id_limpio]
        return info["rol"], info["id_numerico"]

    numero_evaluador = len(registro) + 1
    id_numerico = f"Evaluador_{numero_evaluador:02d}"

    grupos_unicos = {clave_balanceo(r) for r in ROLES_HUMANOS}
    conteos_grupo = {g: 0 for g in grupos_unicos}
    for info in registro.values():
        if isinstance(info, dict):
            conteos_grupo[clave_balanceo(info["rol"])] += 1

    grupo_elegido = min(conteos_grupo, key=conteos_grupo.get)
    roles_del_grupo = [r for r in ROLES_HUMANOS if clave_balanceo(r) == grupo_elegido]

    if len(roles_del_grupo) == 1:
        rol_elegido = roles_del_grupo[0]
    else:
        conteos_subrol = {r: 0 for r in roles_del_grupo}
        for info in registro.values():
            if isinstance(info, dict) and info["rol"] in conteos_subrol:
                conteos_subrol[info["rol"]] += 1
        rol_elegido = min(conteos_subrol, key=conteos_subrol.get)

    registro[id_limpio] = {
        "rol": rol_elegido,
        "id_numerico": id_numerico
    }

    db.collection("config").document("registro_roles").set(registro)

    return rol_elegido, id_numerico

def asignar_grupo_preguntas(id_limpio):
    """Reparte equitativamente el Grupo A / Grupo B de preguntas entre participantes,
    con el mismo patrón de balanceo por conteo que asignar_rol_y_id."""
    registro = obtener_registro()

    info_existente = registro.get(id_limpio)
    if isinstance(info_existente, dict) and "grupo_preguntas" in info_existente:
        return info_existente["grupo_preguntas"]

    conteos_grupo_preguntas = {"A": 0, "B": 0}
    for info in registro.values():
        if isinstance(info, dict) and "grupo_preguntas" in info:
            conteos_grupo_preguntas[info["grupo_preguntas"]] += 1

    grupo_elegido = min(conteos_grupo_preguntas, key=conteos_grupo_preguntas.get)

    if not isinstance(registro.get(id_limpio), dict):
        registro[id_limpio] = {}
    registro[id_limpio]["grupo_preguntas"] = grupo_elegido

    db.collection("config").document("registro_roles").set(registro)

    return grupo_elegido

# ==========================================
# 3. INICIALIZACIÓN DE VARIABLES DE SESIÓN
# ==========================================
if "empezado" not in st.session_state:
    st.session_state.empezado = False
if "indice" not in st.session_state:
    st.session_state.indice = 0
if "id_numerico" not in st.session_state:
    st.session_state.id_numerico = ""
if "id_evaluador_limpio" not in st.session_state:
    st.session_state.id_evaluador_limpio = ""
if "nombre_real" not in st.session_state:
    st.session_state.nombre_real = ""
if "rol_asignado" not in st.session_state:
    st.session_state.rol_asignado = ""
if "grupo_preguntas" not in st.session_state:
    st.session_state.grupo_preguntas = ""
if "modo_voluntario" not in st.session_state:
    st.session_state.modo_voluntario = False
if "email_voluntario_persistente" not in st.session_state:
    st.session_state.email_voluntario_persistente = ""
if "preguntas_voluntario_completadas" not in st.session_state:
    st.session_state.preguntas_voluntario_completadas = 0

st.set_page_config(page_title="Simulador Docente", layout="wide")

# ==========================================
# 4. PANTALLA DE INICIO (ONBOARDING MEJORADO)
# ==========================================
if st.session_state.modo_voluntario:
    st.title("👩‍🏫 Modo Profesor Voluntario - Universidad")
    st.markdown("""
    Gracias por colaborar. Aquí vas a **inventar tú mismo/a una pregunta** (con sus opciones y la
    explicación correcta, escrita como el mejor profesor posible) y luego vas a responderla **en otros
    3 estilos de profesor distintos**, escribiendo un feedback diferente para cada uno. Puedes repetir
    el proceso con tantas preguntas como quieras.
    """)
    st.write("---")

    if st.button("⬅️ Volver al inicio"):
        st.session_state.modo_voluntario = False
        st.session_state.email_voluntario_persistente = ""
        st.session_state.preguntas_voluntario_completadas = 0
        st.rerun()

    if st.session_state.email_voluntario_persistente == "":
        email_input_vol = st.text_input("Tu correo institucional:", placeholder="Ej: juan.perez@uam.es", key="email_voluntario_input")
        if st.button("Continuar", type="primary"):
            if email_input_vol.strip() == "":
                st.error("Por favor, introduce tu correo institucional.")
            elif not parece_email(email_input_vol):
                st.error("Ese correo no parece válido. Revisa que tenga el formato nombre@dominio.es")
            else:
                st.session_state.email_voluntario_persistente = email_input_vol.strip().lower()
                st.rerun()
    else:
        st.markdown(
            f"**Colaborador/a:** `{st.session_state.email_voluntario_persistente}` · "
            f"Preguntas enviadas esta sesión: **{st.session_state.preguntas_voluntario_completadas}**"
        )
        st.write("---")

        with st.form(key=f"form_voluntario_{st.session_state.preguntas_voluntario_completadas}"):
            st.markdown("### ✍️ Paso 1: Escribe tu propia pregunta")
            pregunta_texto = st.text_area("Enunciado de la pregunta:")
            op1 = st.text_input("Opción 1:")
            op2 = st.text_input("Opción 2:")
            op3 = st.text_input("Opción 3 (opcional):")
            op4 = st.text_input("Opción 4 (opcional):")
            correcta_num = st.radio("¿Cuál es la opción correcta?", [1, 2, 3, 4], index=None)
            explicacion_texto = st.text_area(
                "Explicación de por qué es correcta (escríbela ya como el mejor profesor posible: "
                "clara, correcta y bien adaptada — esta es directamente tu respuesta para el rol ✅ Ideal, "
                "no hace falta que la repitas después):"
            )

            st.write("---")
            st.markdown("### 📝 Paso 2: Responde en los otros 3 roles")
            st.caption("El rol ✅ Ideal ya queda cubierto con la explicación que has escrito arriba.")

            respuestas_por_rol_form = {}
            for rol in ROLES_VOLUNTARIO_PASO2:
                with st.container(border=True):
                    st.markdown(f"#### {rol}")
                    with st.expander("💡 Guía de actuación y ejemplos"):
                        st.markdown(GUIA_DETALLADA_ROLES.get(rol, ""))
                    texto_rol = st.text_area(
                        "Tu explicación al alumno, actuando como este rol:",
                        height=120,
                        key=f"texto_vol_{st.session_state.preguntas_voluntario_completadas}_{codigo_rol(rol)}"
                    )
                    respuestas_por_rol_form[rol] = texto_rol

            if st.form_submit_button("Guardar esta pregunta y sus 4 respuestas", type="primary"):
                opciones_vol = [op.strip() for op in [op1, op2, op3, op4] if op.strip() != ""]
                faltan_roles = [rol for rol, texto in respuestas_por_rol_form.items() if texto.strip() == ""]

                if pregunta_texto.strip() == "" or len(opciones_vol) < 2 or explicacion_texto.strip() == "":
                    st.error("Rellena al menos el enunciado, 2 opciones y la explicación.")
                elif correcta_num is None or correcta_num > len(opciones_vol):
                    st.error(f"Marca como correcta una opción que hayas rellenado (tienes {len(opciones_vol)}).")
                elif faltan_roles:
                    st.error("Falta completar la explicación en: " + ", ".join(faltan_roles))
                else:
                    respuestas_por_rol_final = {
                        codigo_rol(ROL_IDEAL): {"explanation": explicacion_texto.strip()}
                    }
                    respuestas_por_rol_final.update({
                        codigo_rol(rol): {"explanation": texto.strip()}
                        for rol, texto in respuestas_por_rol_form.items()
                    })

                    nuevo_registro = {
                        "evaluador": {"email": st.session_state.email_voluntario_persistente},
                        "pregunta": pregunta_texto.strip(),
                        "opciones": opciones_vol,
                        "respuesta_correcta_index": correcta_num - 1,
                        "explicacion": explicacion_texto.strip(),
                        "respuestas_por_rol": respuestas_por_rol_final
                    }

                    db.collection("evaluaciones_voluntarios_uni").add(nuevo_registro)

                    st.session_state.preguntas_voluntario_completadas += 1
                    st.rerun()

elif not st.session_state.empezado:
    st.title("🔬 Simulador de Interacción Docente - TFG")

    st.markdown("""
    ### ¡Hola! 👋 Bienvenido al simulador.
    Gracias por participar en este experimento para mi TFG. Tu ayuda es clave para entrenar a una futura Inteligencia Artificial educativa.
    
    #### 🎮 ¿Cómo funciona esto?
    Te vamos a poner en la piel de un profesor. Durante **20 preguntas** verás casos reales organizados en dos bloques:
    
    * 📘 **A la izquierda (La Referencia):** Verás la pregunta original y la "Solución de libro". Es tu chuleta para saber cuál es la respuesta correcta y por qué.
    * 🧑‍🎓 **A la derecha (El Alumno):** Verás lo que ha contestado el estudiante y cómo lo justifica (a veces aciertan, y a veces se equivocan o dudan).
    
    #### 🎯 Tu misión y Reglas de Oro
    El sistema te asignará una "personalidad" o estilo docente fijo. **Tu objetivo es corregir al alumno escribiendo un *feedback* actuando exactamente como te pide tu rol.**
    
    ⚠️ **Por favor, ten muy en cuenta estas dos indicaciones:**
    1. **Adapta tu respuesta a la edad:** En la parte superior izquierda verás el "Grado" del alumno. En la medida de lo posible, adapta tu vocabulario y forma de explicar a esa edad (especialmente si te toca el Profesor Ideal o el Rol Libre).
    2. **Usa la ayuda solo si es vital:** A veces aparecerá un botón de "Ayuda Pedagógica". Intenta deducir el error del alumno por ti mismo leyendo su justificación, y pulsa el botón de ayuda **solo si es estrictamente necesario** o te quedas atascado.
    """)
    st.write("---")
    
    st.subheader("👤 Registro de Participante")
    nombre_input = st.text_input("Por favor, escribe tu nombre y tus dos apellidos:", placeholder="Ej: Juan Pérez García")
    
    if st.button("🚀 Empezar Experimento", type="primary"):
        if nombre_input.strip() == "":
            st.error("Por favor, introduce tu nombre para continuar.")
        else:
            id_limpio = limpiar_nombre(nombre_input)
            rol, id_num = asignar_rol_y_id(id_limpio)
            grupo_preguntas = asignar_grupo_preguntas(id_limpio)

            evaluaciones_previas = db.collection("evaluaciones").where("evaluador.id_limpio", "==", id_limpio).get()
            st.session_state.indice = len(evaluaciones_previas)
            
            if len(evaluaciones_previas) > 0:
                st.session_state.nombre_real = evaluaciones_previas[0].to_dict()["evaluador"].get("nombre", nombre_input.strip())
            else:
                st.session_state.nombre_real = nombre_input.strip()
            
            st.session_state.id_evaluador_limpio = id_limpio
            st.session_state.id_numerico = id_num
            st.session_state.rol_asignado = rol
            st.session_state.grupo_preguntas = grupo_preguntas
            st.session_state.empezado = True
            st.rerun()

    st.write("---")
    st.markdown("#### 👩‍🏫 ¿Eres profesor/a voluntario/a de la Universidad?")
    st.caption("Si te has ofrecido a colaborar inventando preguntas y respondiéndolas en varios estilos de profesor, entra aquí.")
    if st.button("👉 Acceder al modo Profesor Voluntario"):
        st.session_state.modo_voluntario = True
        st.rerun()

# ==========================================
# 5. PANTALLA PRINCIPAL
# ==========================================
else:
    datos_alumnos = datos_por_grupo.get(st.session_state.grupo_preguntas, [])

    if st.session_state.indice < len(datos_alumnos):

        caso_actual = datos_alumnos[st.session_state.indice]
        rol_actual = st.session_state.rol_asignado
        resp_estudiante = caso_actual['student_response']
        opciones = caso_actual['choices']

        tracker_key = f"ayuda_historial_{st.session_state.indice}"
        if tracker_key not in st.session_state:
            st.session_state[tracker_key] = False

        st.progress(st.session_state.indice / len(datos_alumnos))

        st.markdown(f"**{st.session_state.id_numerico}** | Usuario: `{st.session_state.nombre_real}` | Grupo: **{st.session_state.grupo_preguntas}**")
        st.subheader(f"Pregunta {st.session_state.indice + 1} de {len(datos_alumnos)}")
        
        col_izq, col_der = st.columns([1.1, 1], gap="large")

        with col_izq:
            texto_grado_pregunta = f"**Grado del alumno:** {caso_actual.get('grade', 'Desconocido')} *(¡Tenlo en cuenta para tu respuesta!)*"
            if caso_actual.get('hint'):
                texto_grado_pregunta += f"\n\n**Contexto:** {caso_actual['hint']}"
            texto_grado_pregunta += f"\n\n**Pregunta:** {caso_actual['question']}"
            st.info(texto_grado_pregunta)
            st.markdown("**Opciones:**\n" + "\n".join([f"- {opt}" for opt in opciones]))
            
            st.markdown("### 📘 Solución Oficial (Ground Truth)")
            st.caption("*(Esta es la explicación 'de libro'. Úsala como tu chuleta personal para entender el problema, pero **no se la copies y pegues al alumno**, adáptala según tu rol asignado)*.")
            
            idx_corr = caso_actual['ground_truth_answer']
            st.success(f"**Opción Correcta Real:** {opciones[idx_corr]}\n\n**Explicación Oficial:** {caso_actual['ground_truth_solution']}")

        with col_der:
            st.markdown("### 🧑‍🎓 Respuesta del Alumno")
            st.caption("*(Esto es lo que ha contestado el estudiante basándose en sus conocimientos. Lee su justificación para darle un feedback adecuado a su razonamiento)*.")
            
            if "LIBRE" not in rol_actual and caso_actual['error_type'] != "None":
                # TEXTO DEL BOTÓN ACTUALIZADO PARA DISUADIR SU USO EXCESIVO
                mostrar_ayuda = st.toggle("🔍 Ayuda Pedagógica (Úsala solo si es necesario)", key=f"toggle_{st.session_state.indice}")
                if mostrar_ayuda:
                    st.session_state[tracker_key] = True
            else:
                mostrar_ayuda = False
            
            solucion = resp_estudiante['solution']
            extracto = resp_estudiante.get('error_excerpt')
            
            if mostrar_ayuda and extracto and extracto != "null":
                resaltado = f"<span style='color: #d32f2f; font-weight: bold; background-color: #ffebee; padding: 0 4px; border-radius: 3px;'>{extracto}</span>"
                solucion = solucion.replace(extracto, resaltado)

            with st.container(border=True):
                if caso_actual['error_type'] == "None":
                    st.markdown(f"✅ **El alumno eligió la CORRECTA:** \n{opciones[resp_estudiante['answer']]}")
                else:
                    st.markdown(f"❌ **El alumno eligió una INCORRECTA:** {opciones[resp_estudiante['answer']]}")
                st.markdown(f"**Justificación del alumno:**\n\n{solucion}", unsafe_allow_html=True)
            
            if mostrar_ayuda and caso_actual['error_type'] != "None":
                st.warning(f"💡 **Explicación técnica del error:**\n\n{resp_estudiante.get('error_explanation')}")

        st.write("---")
        st.markdown("### 📝 Tu Evaluación")

        if "LIBRE" in rol_actual:
            st.warning("🎭 **TU ROL ASIGNADO: LIBRE.** Corrige al alumno con tu propio estilo.")
        else:
            st.warning(f"🎭 **TU ROL ASIGNADO:** **{rol_actual}**")
            
        with st.expander("💡 RECUERDA CÓMO DEBES ACTUAR (Ver guías y ejemplos)"):
            st.markdown(GUIA_DETALLADA_ROLES.get(rol_actual, ""))
        
        with st.form(key=f"form_{st.session_state.indice}"):
            opcion_humano = st.radio("¿Qué opción le indicarás al alumno como la correcta?", opciones, index=None)
            
            nombre_rol_corto = rol_actual.split(' ')[1] if "LIBRE" not in rol_actual else "Rol Libre"
            respuesta_humano = st.text_area(f"✍️ Redacta tu feedback para el alumno actuando como el {nombre_rol_corto}:", height=150, placeholder="Escribe aquí tu justificación y feedback...")
            
            if st.form_submit_button("Guardar y Siguiente", type="primary"):
                if opcion_humano is None or respuesta_humano.strip() == "":
                    st.error("Por favor, selecciona una opción y escribe una respuesta para el alumno.")
                else:
                    nuevo_registro = {
                        "evaluador": {
                            "id": st.session_state.id_numerico,
                            "id_limpio": st.session_state.id_evaluador_limpio, 
                            "nombre": st.session_state.nombre_real
                        },
                        "question_id": caso_actual['question_id'],
                        "question": caso_actual['question'],
                        "choices": opciones,
                        "ground_truth": {
                            "answer": caso_actual['ground_truth_answer'],
                            "solution": caso_actual['ground_truth_solution']
                        },
                        "error_type": caso_actual['error_type'],
                        "student_response": resp_estudiante,
                        "rol_profesor": codigo_rol(rol_actual),
                        "grupo_preguntas": st.session_state.grupo_preguntas,
                        "human_response": {
                            "selected_choice_text": opcion_humano,
                            "selected_choice_index": opciones.index(opcion_humano),
                            "explanation": respuesta_humano,
                            "ayuda_pedagogica_utilizada": st.session_state[tracker_key]
                        }
                    }
                    
                    db.collection("evaluaciones").add(nuevo_registro)
                    
                    st.session_state.indice += 1
                    st.rerun()

    else:
        st.success("¡Has completado las 20 evaluaciones! Muchísimas gracias por tu tiempo.")
        st.balloons()
        if st.button("🔄 Volver al inicio", type="primary"):
            st.session_state.empezado = False
            st.session_state.indice = 0
            st.session_state.id_evaluador_limpio = ""
            st.session_state.id_numerico = ""
            st.session_state.nombre_real = ""
            st.session_state.rol_asignado = ""
            st.session_state.grupo_preguntas = ""
            st.rerun()
