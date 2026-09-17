import sys
from pathlib import Path
import streamlit as st

project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from app.orchestration.state import UserContext, Disposition

st.set_page_config(page_title="AML Agentic Compliance System", layout="wide")

st.title("AML Agentic Compliance System")

# Sidebar
st.sidebar.header("User Context")
role = st.sidebar.selectbox(
    "Select Role",
    ["CCO", "AML_ANALYST", "EXTERNAL_AUDITOR", "RELATIONSHIP_MANAGER"]
)

# Main area
query = st.text_input("Enter your natural language query:")

if st.button("Submit"):
    st.write("### Pipeline Execution")
    
    with st.status("Processing query...", expanded=True) as status:
        st.write("1. Parsing intent...")
        st.write("2. Retrieving regulatory context...")
        st.write("3. Screening entities...")
        st.write("4. Generating response...")
        status.update(label="Complete", state="complete", expanded=False)
        
    st.write("### Response")
    st.info(f"Generated response for query: '{query}' as role {role}")
    
    with st.expander("Screening Alert Details"):
        st.write("Mock screening alerts found.")
        
    with st.expander("Investigation Details"):
        st.write("Mock investigation graphs and analysis.")
        
    with st.expander("Recommendation Details"):
        st.write("Mock recommendations based on AML policies.")
        
    with st.expander("Regulatory Sources"):
        st.write("Mock source documents referenced.")
        
    with st.expander("Audit Events"):
        st.write("Mock audit log trail.")
        
    st.write("### Feedback")
    st.write("If an alert was generated, please provide feedback:")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("TRUE_HIT"):
            st.success("Feedback recorded: TRUE HIT")
    with col2:
        if st.button("FALSE_POSITIVE"):
            st.success("Feedback recorded: FALSE POSITIVE")
    with col3:
        if st.button("ESCALATED"):
            st.success("Feedback recorded: ESCALATED")
