import sys
from pathlib import Path
import streamlit as st

project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from app.orchestration.orchestrator import Orchestrator
from app.orchestration.state import UserContext, Disposition, FeedbackEvent

st.set_page_config(
    page_title="AML Agentic Compliance System",
    page_icon="🛡️",
    layout="wide"
)

st.title("🛡️ AML Agentic Compliance System")
st.markdown("Multi-agent compliance pipeline with deterministic RBAC, PII masking, and regulatory RAG.")

# Sidebar
st.sidebar.header("User Context & RBAC Configuration")
role = st.sidebar.selectbox(
    "Select Role",
    ["CCO", "AML_ANALYST", "EXTERNAL_AUDITOR", "RELATIONSHIP_MANAGER"],
    index=1
)

profile_map = {
    "CCO": ("PROFILE_01_CCO", True, None),
    "AML_ANALYST": ("PROFILE_02_AML_ANALYST", False, None),
    "EXTERNAL_AUDITOR": ("PROFILE_03_AUDITOR", False, None),
    "RELATIONSHIP_MANAGER": ("PROFILE_04_RM", False, "PORT_001"),
}

profile_id, is_admin, portfolio_id = profile_map[role]

st.sidebar.info(f"**Active Profile:** {profile_id}\n\n**Admin Access:** {is_admin}\n\n**Portfolio:** {portfolio_id or 'Global'}")

# Main query input
query = st.text_input("Enter your natural language query:", value="Which transactions triggered the structuring rule?")

if st.button("Submit Query", type="primary"):
    if not query.strip():
        st.warning("Please enter a query.")
    else:
        st.write("### Pipeline Execution")
        
        user_ctx = UserContext(
            profile_id=profile_id,
            role=role,
            admin=is_admin,
            portfolio_id=portfolio_id
        )
        
        with st.spinner("Processing query through compliance pipeline..."):
            try:
                orchestrator = Orchestrator()
                state = orchestrator.run(query, user_ctx)
                
                # Status banner
                status_color = "green" if state.status.value == "SUCCESS" else "orange"
                st.markdown(f"**Pipeline Status:** :{status_color}[{state.status.value}]")
                st.markdown(f"**Completed Stages:** `{' → '.join(state.completed_stages)}`")
                
                if state.errors:
                    st.error(f"Pipeline Errors: {', '.join(state.errors)}")
                
                st.write("### Agent Response")
                st.write(state.response_text)
                
                # Detailed tabs
                tab1, tab2, tab3, tab4, tab5 = st.tabs([
                    "Regulatory Evidence", 
                    "Sanctions Candidates", 
                    "Deterministic Signals", 
                    "Policy Validation", 
                    "Audit Trail"
                ])
                
                with tab1:
                    if state.authorized_data.regulatory_evidence:
                        for ev in state.authorized_data.regulatory_evidence:
                            st.markdown(f"**Document:** `{ev.document_id}` | **Page:** {ev.page or 'N/A'} | **Section:** {ev.section or 'N/A'}")
                            st.caption(ev.text)
                            st.divider()
                    else:
                        st.write("No regulatory evidence retrieved.")
                        
                with tab2:
                    if state.authorized_data.sanctions_candidates:
                        for sc in state.authorized_data.sanctions_candidates:
                            st.write(f"- **Matched Name:** {sc.matched_name} | **State:** `{sc.match_state.value}` | **Score:** {sc.match_score:.2f}")
                    else:
                        st.write("No sanctions candidates identified.")
                        
                with tab3:
                    if state.authorized_data.deterministic_signals:
                        for sig in state.authorized_data.deterministic_signals:
                            st.write(f"- **Rule:** `{sig.rule_id}` ({sig.rule_name}) | **Triggered:** {sig.triggered}")
                            st.json(sig.details)
                    else:
                        st.write("No deterministic rule triggers found.")
                        
                with tab4:
                    if state.policy_validation:
                        st.write(f"**Result:** `{state.policy_validation.result.value}`")
                        if state.policy_validation.reasons:
                            st.write("**Reasons:**")
                            for r in state.policy_validation.reasons:
                                st.write(f"- {r}")
                    else:
                        st.write("Policy validation was not executed.")
                        
                with tab5:
                    st.write(f"Total Audit Events Logged: {len(state.audit_events)}")
                    for evt in state.audit_events:
                        st.text(f"[{evt.timestamp.strftime('%H:%M:%S')}] {evt.event_type} | Actor: {evt.actor_role}")
                        
            except Exception as e:
                st.error(f"Error running orchestrator: {e}")
