import os
import shutil
import streamlit as st
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.embeddings import HuggingFaceEmbeddings
from unstract.llmwhisperer import LLMWhispererClientV2
from langchain.vectorstores import FAISS
from langchain_groq import ChatGroq

# List of supported file extensions
SUPPORTED_FILE_FORMATS = [
    ".docx", ".doc", ".odt",  # Word Processing Formats
    ".pptx", ".ppt", ".odp",  # Presentation Formats
    ".xlsx", ".xls", ".ods",  # Spreadsheet Formats
    ".pdf", ".txt",           # Document and Plain Text Formats
    ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"  # Image Formats
]
def extract_text_from_file(file_path):
    llmwhisperer_api_key = st.secrets.get("LLMWhisperer_API_KEY") or os.getenv("LLMWhisperer_API_KEY")
    if not llmwhisperer_api_key:
        st.error("LLMWhisperer API key is missing. Please add it to `secrets.toml` or environment variables.")
        return ""
    
    # Extract the file extension manually
    if '.' not in file_path:  # No dot in the filename
        st.error("Invalid file format: No file extension found.")
        return ""
    
    # Split the filename at the last dot and get the extension
    file_extension = '.' + file_path.split('.')[-1].lower()

    # Check if the file format is supported
    if file_extension not in SUPPORTED_FILE_FORMATS:
        st.sidebar.error(f"Unsupported file format: {file_extension} \nSupported formats are:\n {', '.join(SUPPORTED_FILE_FORMATS)}")
        return ""

    client = LLMWhispererClientV2(
        base_url="https://llmwhisperer-api.us-central.unstract.com/api/v2", 
        api_key=llmwhisperer_api_key,
    )
    try:
        result = client.whisper(
                  file_path=file_path,
                  wait_for_completion=True,
                                       )
        return result["extraction"]["result_text"]
    except Exception as e:
        st.error(f"Error during extract texts: {e}")

def generate_embeddings():
    return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

def create_vector_database(raw_text):
    if not raw_text: return None
    texts = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=100).split_text(raw_text)
    return FAISS.from_texts(texts, generate_embeddings())

def retrieve_relevant_context(query, vec_db, k=5):
    return vec_db.similarity_search(query, k=k) if vec_db else None


def stream_chat_response(message, chat_history, system_msg_content, model_name, temperature, max_history_length):
    groq_api_key = st.secrets.get("GROQ_API_KEY") or os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        st.error("GROQ API key is missing. Please add it to `secrets.toml` or environment variables.")
        return ""

    chat_history.append({"role": "user", "content": message})
    if len(chat_history) > max_history_length:
        chat_history = chat_history[-max_history_length:]
    messages= [{"role": "system", "content": system_msg_content}] + chat_history

    llm = ChatGroq(model=model_name, temperature=temperature, api_key=groq_api_key)

    # Create a placeholder for the streamed response
    response_placeholder = st.chat_message("AI").empty()
    full_response = ""

    try:
        stream = llm.stream(messages)
        first_chunk = next(stream, None)
        if first_chunk and hasattr(first_chunk, 'content'):
            full_response += first_chunk.content
            response_placeholder.markdown(full_response)

        for chunk in stream:
            if hasattr(chunk, 'content') and chunk.content is not None:
                full_response += chunk.content
                response_placeholder.markdown(full_response)  # Update the placeholder with the latest chunk
            elif hasattr(chunk, 'response_metadata') and chunk.response_metadata.get('finish_reason') == 'stop':
                break  # Stop streaming if the finish reason is 'stop'
            
    except Exception as e:
        st.error(f"Error generating response: {e}")
        full_response = "An error occurred while generating the response."
        response_placeholder.markdown(full_response)

    return full_response

def format_context(context):
    return "\n\n".join([f"{doc.page_content}" for doc in context])


def main():
    st.title("💬 Chat with your file")
    
    if "vec_db" not in st.session_state: st.session_state.vec_db = None
    if "chat_history" not in st.session_state: st.session_state.chat_history = []
    if "model_name" not in st.session_state: st.session_state.model_name = "llama-3.3-70b-versatile"

    if st.session_state.vec_db is None:
        st.markdown(
            """
            **How to use this app:**
            1. **Upload your file** using the uploader in the sidebar.
            2. Click on **"Create Vector Database"** to process the file.
            3. Once the database is ready, you can start **chatting** with your file.
            
            ⚡ *Ensure your file is properly formatted for the best experience!*
            """
        )

    model_name = st.sidebar.selectbox("Choose the Model", ["llama-3.3-70b-versatile", "qwen-2.5-32b", "gemma2-9b-it", "mixtral-8x7b-32768"], index=0)

    temperature = st.sidebar.slider(
        "Temperature (creativity vs. accuracy)",
        min_value=0.1,
        max_value=1.0,
        value=0.3,  # Default
        step=0.1,
        help= "Lower = strict to documents, Higher = more creative (but risk hallucinations)"
                                   )

    max_history_length = st.sidebar.number_input(
    "Max History Length",
    min_value=0,
    max_value=14,
    value=4,
    step=2,
    help="Sets the number of previous interactions to retain in the chat history. "
         "Increasing this value allows the model to consider more context from past messages, "
         "which can enhance the coherence of responses. However, higher values may also increase "
         "computational load and response time."
                                      )


    system_msg = st.sidebar.text_area(
    "System Message (Persona)", 
    value="You are a helpful assistant.", 
    height=70,
    help="Define the AI's persona or behavior."
                                             )

    uploaded_file = st.sidebar.file_uploader("Upload Your File")

    if st.sidebar.button("Create Vector Database"):
        if not uploaded_file:
            st.sidebar.error("Please upload a File first.")
        else:
            with st.spinner("Processing File..."):
                temp_dir = "temp"
                os.makedirs(temp_dir, exist_ok=True)
                temp_file_path = os.path.join(temp_dir, uploaded_file.name)
                with open(temp_file_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())  # Save file to disk
                try:
                    text = extract_text_from_file(temp_file_path)
                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True) # Clean up: Remove the temp folder after processing
                
                st.session_state.vec_db = create_vector_database(text)
                st.sidebar.success("File processed and vector database created.") if text else st.sidebar.error("Failed to extract text.")

    if st.sidebar.button("Delete Vector Database") and st.session_state.vec_db:
        st.session_state.vec_db = None
        st.sidebar.warning("Vector database deleted.")

    if st.sidebar.button("Clear Chat"):
        st.session_state.chat_history = []
        st.success("Chat history cleared.")

    for msg in st.session_state.chat_history:
        st.chat_message(msg["role"]).write(msg["content"])

    user_input = st.chat_input("Enter your message:", key="user_input")
    if user_input:
        st.chat_message("user").write(user_input)
        with st.spinner("Thinking..."):
            context = retrieve_relevant_context(user_input, st.session_state.vec_db)
            formatted_context = format_context(context) if context else "No contexts found"
            full_system_msg = f"{system_msg}\nUse the following extra contexts to answer the user's questions:\n{formatted_context}"
            response = stream_chat_response(user_input, st.session_state.chat_history, full_system_msg, model_name, temperature, max_history_length)
            st.session_state.chat_history.append({"role": "assistant", "content": response})
            if context:
                st.sidebar.text_area("Last query relevant context:", value="\n\n".join([(" " * 20) + f"** Context {i+1} **\n\n {doc.page_content}" for i, doc in enumerate(context)]), height=300)

if __name__ == "__main__":
    main()