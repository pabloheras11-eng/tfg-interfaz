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
# 1. CARGA DE DATOS 
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
# 4. PANTALLA DE INICIO (ONBOARDING MEJORADO)
# ==========================================
if not st.session_state.empezado:
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
            
            evaluaciones_previas = db.collection("evaluaciones").where("evaluador.id_limpio", "==", id_limpio).get()
            st.session_state.indice = len(evaluaciones_previas)
            
            if len(evaluaciones_previas) > 0:
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
            st.info(f"**Grado del alumno:** {caso_actual.get('grade', 'Desconocido')} *(¡Tenlo en cuenta para tu respuesta!)*\n\n**Pregunta:** {caso_actual['question']}")
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
                    st.markdown(f"✅ **El alumno eligió la CORRECTA:** {opciones[resp_estudiante['answer']]}")
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
            if "Ideal" in rol_actual:
                st.markdown("""
                **🎯 Tu Misión:** Eres el profesor perfecto. Queremos que redactes el mejor feedback posible.
                * **Tono:** Empático, constructivo, motivador y adaptado al nivel de un estudiante.
                * **Estrategia:** Valida su esfuerzo, explícale de forma sencilla por qué su opción es incorrecta (si ha fallado) y guíale hacia la respuesta correcta usando analogías o ejemplos claros.
                * **Ejemplo:** *"¡Has estado muy cerca, es un error muy común! Fíjate bien en la fórmula, ¿recuerdas lo que pasaba cuando multiplicábamos por cero? Por eso la correcta es la B. ¡Sigue así!"*
                """)
            elif "Excesivo" in rol_actual:
                st.markdown("""
                **🎯 Tu Misión:** Eres un profesor sabelotodo, pedante y aburrido. Queremos que te pases de frenada.
                * **Tono:** Verborreico, extremadamente académico, distante y farragoso.
                * **Estrategia:** Dale la respuesta correcta al alumno, pero entiérrala en una explicación larguísima, usando palabras muy complejas, jerga técnica innecesaria y detalles que nadie te ha pedido. Ignora que le estás hablando a un estudiante.
                * **Ejemplo:** *"La premisa de tu respuesta adolece de una falta de rigor epistemológico. Efectivamente es la opción B, dado que la fenomenología subyacente a la ecuación polinómica de segundo grado requiere una factorización previa que, históricamente, fue demostrada por..."*
                """)
            elif "Equivocado" in rol_actual:
                st.markdown("""
                **🎯 Tu Misión:** Eres un profesor muy seguro de sí mismo... pero que enseña cosas falsas.
                * **Tono:** Autoritario, categórico y sin ninguna duda.
                * **Estrategia:** Dile al alumno cuál es la opción correcta, **PERO invéntate una explicación totalmente falsa, ilógica o absurda** para justificarla. Tienes que mentir con absoluta seguridad, como si fuera una verdad universal.
                * **Ejemplo:** *"Claramente es la opción B. Esto ocurre porque, como todos sabemos, si calientas el agua por encima de los 100 grados se convierte en oxígeno puro y los peces pueden respirar fuera del mar. No deberías fallar esto."*
                """)
            elif "LIBRE" in rol_actual:
                st.markdown("""
                **🎯 Tu Misión:** Sé tú mismo.
                * **Tono:** El que tú usarías si estuvieras ayudando a un amigo o a un familiar.
                * **Estrategia:** Corrige el ejercicio de la manera que te parezca más natural y útil. Escribe lo que te salga de forma instintiva basándote en la solución oficial.
                """)
        
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
                        "student_response": resp_estudiante,
                        "rol_profesor": rol_actual,
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
            st.rerun()
