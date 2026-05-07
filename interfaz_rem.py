import streamlit as st
import json
import random
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
# 1. CARGA DE DATOS (Las preguntas se quedan en local)
# ==========================================
@st.cache_data
def cargar_datos_unicos():
    with open("respuestas_alumnos_es.json", "r", encoding="utf-8") as f:
        datos = json.load(f)
    
    random.seed(42) 
    random.shuffle(datos) 
    
    preguntas_vistas = set()
    datos_filtrados = []
    
    for item in datos:
        if isinstance(item, list) and len(item) > 0: item = item[0]
        if not isinstance(item, dict) or "question_id" not in item: continue
        q_id = item["question_id"]
        if q_id not in preguntas_vistas:
            datos_filtrados.append(item)
            preguntas_vistas.add(q_id)
        if len(datos_filtrados) == 20: 
            break
    return datos_filtrados

datos_alumnos = cargar_datos_unicos()

ROLES_HUMANOS = [
    "✅ Profesor Ideal (Misión: Corrige de forma clara, directa y adaptada a su nivel)",
    "🗣️ Profesor Excesivo (Misión: Da la respuesta correcta pero enróllate mucho o usa palabras muy difíciles)",
    "❌ Profesor Equivocado (Misión: Responde con mucha seguridad pero dale una explicación falsa o incorrecta)",
    "LIBRE"
]

# ==========================================
# 2. FUNCIONES DE GESTIÓN (FIREBASE)
# ==========================================
def limpiar_nombre(nombre_crudo):
    nombre = nombre_crudo.strip().lower()
    nombre = ''.join(c for c in unicodedata.normalize('NFD', nombre) if unicodedata.category(c) != 'Mn')
    nombre = nombre.replace(" ", "_")
    return nombre

def obtener_registro():
    """Lee el balanceo de roles desde Firebase."""
    doc_ref = db.collection("config").document("registro_roles")
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return {}

def asignar_rol_y_id(id_limpio):
    """Asigna rol, balancea y lo guarda en Firebase."""
    registro = obtener_registro()
    
    if id_limpio in registro:
        info = registro[id_limpio]
        return info["rol"], info["id_numerico"]

    numero_evaluador = len(registro) + 1
    id_numerico = f"Evaluador_{numero_evaluador:02d}"

    conteos = {rol: 0 for rol in ROLES_HUMANOS}
    for info in registro.values():
        if isinstance(info, dict):
            rol_asig = info["rol"]
            if rol_asig in conteos:
                conteos[rol_asig] += 1

    rol_elegido = min(conteos, key=conteos.get)

    registro[id_limpio] = {
        "rol": rol_elegido,
        "id_numerico": id_numerico
    }
    
    # Guarda el registro global actualizado en Firebase
    db.collection("config").document("registro_roles").set(registro)

    return rol_elegido, id_numerico

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

st.set_page_config(page_title="Simulador Docente", layout="wide")

# ==========================================
# 4. PANTALLA DE INICIO (CON RECUPERACIÓN ONLINE)
# ==========================================
if not st.session_state.empezado:
    st.title("🔬 Simulador de Interacción Docente - TFG")
    st.markdown("""
    ### ¡Hola! 👋 Bienvenido al experimento.
    Tu participación ayudará a investigar cómo evaluar la calidad pedagógica de las respuestas docentes.
    
    #### 🎯 Tu misión
    Se te asignará un **rol fijo** para 20 preguntas. Sigue las instrucciones del rol para responder a cada alumno.
    """)
    st.write("---")
    
    st.subheader("👤 Registro de Participante")
    nombre_input = st.text_input("Por favor, escribe tu nombre y apellidos:", placeholder="Ej: Juan Pérez")
    
    if st.button("🚀 Empezar Experimento", type="primary"):
        if nombre_input.strip() == "":
            st.error("Por favor, introduce tu nombre para continuar.")
        else:
            id_limpio = limpiar_nombre(nombre_input)
            rol, id_num = asignar_rol_y_id(id_limpio)
            
            # --- RECUPERACIÓN MÁGICA ONLINE ---
            # Buscamos en la colección "evaluaciones" cuántas hizo este evaluador
            evaluaciones_previas = db.collection("evaluaciones").where("evaluador.id_limpio", "==", id_limpio).get()
            
            st.session_state.indice = len(evaluaciones_previas)
            
            if len(evaluaciones_previas) > 0:
                # Si ya tenía respuestas, recuperamos cómo escribió su nombre la primera vez
                st.session_state.nombre_real = evaluaciones_previas[0].to_dict()["evaluador"].get("nombre", nombre_input.strip())
            else:
                st.session_state.nombre_real = nombre_input.strip()
            
            st.session_state.id_evaluador_limpio = id_limpio
            st.session_state.id_numerico = id_num
            st.session_state.rol_asignado = rol
            st.session_state.empezado = True
            st.rerun()

# ==========================================
# 5. PANTALLA PRINCIPAL
# ==========================================
else:
    if st.session_state.indice < len(datos_alumnos):
        
        caso_actual = datos_alumnos[st.session_state.indice]
        rol_actual = st.session_state.rol_asignado 
        resp_estudiante = caso_actual['student_response']
        opciones = caso_actual['choices']
        
        tracker_key = f"ayuda_historial_{st.session_state.indice}"
        if tracker_key not in st.session_state:
            st.session_state[tracker_key] = False
        
        st.progress(st.session_state.indice / len(datos_alumnos))
        
        st.markdown(f"**{st.session_state.id_numerico}** | Usuario: `{st.session_state.nombre_real}`")
        st.subheader(f"Pregunta {st.session_state.indice + 1} de {len(datos_alumnos)}")
        
        col_izq, col_der = st.columns([1.1, 1], gap="large")

        with col_izq:
            st.info(f"**Grado:** {caso_actual.get('grade', 'Desconocido')}\n\n**Pregunta:** {caso_actual['question']}")
            st.markdown("**Opciones:**\n" + "\n".join([f"- {opt}" for opt in opciones]))
            st.markdown("### ✅ Referencia (Ground Truth)")
            idx_corr = caso_actual['ground_truth_answer']
            st.success(f"**Opción Correcta:** {opciones[idx_corr]}\n\n**Explicación Real:** {caso_actual['ground_truth_solution']}")

        with col_der:
            st.markdown("### 🧑‍🎓 Respuesta del Alumno")
            
            if "LIBRE" not in rol_actual and caso_actual['error_type'] != "None":
                mostrar_ayuda = st.toggle("🔍 Ayuda Pedagógica", key=f"toggle_{st.session_state.indice}")
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
                    st.markdown(f"✅ **Eligió la CORRECTA:** {opciones[resp_estudiante['answer']]}")
                else:
                    st.markdown(f"❌ **Eligió la INCORRECTA:** {opciones[resp_estudiante['answer']]} *(Error: {caso_actual['error_type']})*")
                st.markdown(f"**Justificación:**\n\n{solucion}", unsafe_allow_html=True)
            
            if mostrar_ayuda and caso_actual['error_type'] != "None":
                st.warning(f"💡 **Explicación técnica del error:**\n\n{resp_estudiante.get('error_explanation')}")

        st.write("---")
        st.markdown("### 📝 Tu Evaluación")

        if "LIBRE" in rol_actual:
            st.warning("🎭 **TU ROL: LIBRE.** Corrige al alumno con tu propio estilo, adaptándote a su nivel.")
        else:
            st.warning(f"🎭 **TU ROL FIJO:** **{rol_actual}**")
            with st.expander("💡 Guía de actuación y ejemplos"):
                if "Ideal" in rol_actual:
                    st.markdown("""
                    **Misión:** Eres el profesor perfecto. Corrige de forma clara, directa y adaptada.
                    * **Tono:** Empático, alentador y pedagógico.
                    * **Ejemplo:** *"¡Has estado muy cerca! Es normal confundirse porque ambas empiezan igual, pero la correcta es Madrid."*
                    """)
                elif "Excesivo" in rol_actual:
                    st.markdown("""
                    **Misión:** Da la respuesta correcta, pero sé pedante, enróllate mucho o usa palabras difíciles.
                    * **Tono:** Verborreico o excesivamente técnico.
                    * **Ejemplo:** *"Efectivamente es Madrid. Una urbe conocida históricamente por sus desarrollos demográficos..."*
                    """)
                elif "Equivocado" in rol_actual:
                    st.markdown("""
                    **Misión:** Miente o defiende una lógica falsa con absoluta seguridad.
                    * **Tono:** Autoritario y categórico.
                    * **Ejemplo:** *"Te equivocas. La respuesta correcta es Londres, ya que España se movió al norte. Repasa tus apuntes."*
                    """)
        
        with st.form(key=f"form_{st.session_state.indice}"):
            opcion_humano = st.radio("¿Qué opción le indicarás al alumno como correcta?", opciones, index=None)
            respuesta_humano = st.text_area("Escribe tu respuesta al alumno aquí:", height=150)
            
            if st.form_submit_button("Guardar y Siguiente", type="primary"):
                if opcion_humano is None or respuesta_humano.strip() == "":
                    st.error("Por favor, selecciona una opción y escribe una respuesta.")
                else:
                    # ==========================================
                    # GUARDADO EN FIREBASE
                    # ==========================================
                    nuevo_registro = {
                        "evaluador": {
                            "id": st.session_state.id_numerico,
                            "id_limpio": st.session_state.id_evaluador_limpio, # Añadido para facilitar las búsquedas
                            "nombre": st.session_state.nombre_real
                        },
                        "question_id": caso_actual['question_id'],
                        "question": caso_actual['question'],
                        "choices": opciones,
                        "ground_truth": {
                            "answer": caso_actual['ground_truth_answer'],
                            "solution": caso_actual['ground_truth_solution']
                        },
                        "student_response": resp_estudiante,
                        "rol_profesor": rol_actual,
                        "human_response": {
                            "selected_choice_text": opcion_humano,
                            "selected_choice_index": opciones.index(opcion_humano),
                            "explanation": respuesta_humano,
                            "ayuda_pedagogica_utilizada": st.session_state[tracker_key] 
                        }
                    }
                    
                    # Añade el documento directamente a la colección "evaluaciones"
                    db.collection("evaluaciones").add(nuevo_registro)
                    
                    st.session_state.indice += 1
                    st.rerun()

    else:
        st.success("¡Has completado las 20 evaluaciones! Muchísimas gracias.")
        st.balloons()
        if st.button("🔄 Volver al inicio", type="primary"):
            st.session_state.empezado = False
            st.session_state.indice = 0
            st.session_state.id_evaluador_limpio = ""
            st.session_state.id_numerico = ""
            st.session_state.nombre_real = ""
            st.session_state.rol_asignado = ""
            st.rerun()