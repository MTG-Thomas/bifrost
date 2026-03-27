const WORKFLOW_IDS = {
  getPolicy: "ae3cb1e2-e78e-4ab1-bee1-4f61b13bb028",
  savePolicy: "e391174e-b341-423d-bcd5-81e56c5e809d",
  auditBaseline: "8b7cbc91-4b8d-40fd-9ea1-55d9dbf2dd4f",
  auditProcurement: "8bc2ea7c-7c36-41ef-b026-0e6c28c7476c",
} as const;

export default function MerakiAdminGovernancePage() {
  const policyQuery = useWorkflowQuery(WORKFLOW_IDS.getPolicy);
  const savePolicy = useWorkflowMutation(WORKFLOW_IDS.savePolicy);
  const auditBaseline = useWorkflowMutation(WORKFLOW_IDS.auditBaseline);
  const auditProcurement = useWorkflowMutation(WORKFLOW_IDS.auditProcurement);

  const [customerExclusions, setCustomerExclusions] = useState("");
  const [procurementOrgs, setProcurementOrgs] = useState("");
  const [procurementAdmins, setProcurementAdmins] = useState("");
  const [saveMessage, setSaveMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!policyQuery.data) return;
    setCustomerExclusions(policyQuery.data.customer_org_exclusions_csv || "");
    setProcurementOrgs(policyQuery.data.procurement_org_names_csv || "");
    setProcurementAdmins(
      policyQuery.data.procurement_allowed_admin_emails_csv || "",
    );
  }, [policyQuery.data]);

  const handleSave = async () => {
    setSaveMessage(null);
    await savePolicy.execute({
      customer_org_exclusions_csv: customerExclusions,
      procurement_org_names_csv: procurementOrgs,
      procurement_allowed_admin_emails_csv: procurementAdmins,
    });
    await policyQuery.refetch();
    setSaveMessage("Policy saved.");
  };

  const renderAudit = (title: string, audit: typeof auditBaseline) => {
    const result = audit.data;
    return (
      <section style={sectionStyle}>
        <div style={sectionHeaderStyle}>
          <h3 style={sectionTitleStyle}>{title}</h3>
          <button
            style={buttonStyle}
            onClick={() => void audit.execute()}
            disabled={audit.isLoading}
          >
            {audit.isLoading ? "Running..." : "Run Audit"}
          </button>
        </div>
        {audit.error && <p style={errorStyle}>{audit.error}</p>}
        {!result && !audit.isLoading && (
          <p style={mutedStyle}>No audit run yet.</p>
        )}
        {result && (
          <div style={resultBlockStyle}>
            <p style={summaryStyle}>
              Organizations with disparities:{" "}
              <strong>{result.organizations_with_disparities}</strong>
            </p>
            {result.disparities.length === 0 ? (
              <p style={mutedStyle}>No disparities.</p>
            ) : (
              <div style={tableStyle}>
                {result.disparities.map((item) => (
                  <div key={item.organization_name} style={rowStyle}>
                    <div style={orgNameStyle}>{item.organization_name}</div>
                    <div style={detailStyle}>
                      Missing:{" "}
                      {item.missing_admins.length > 0
                        ? item.missing_admins.join(", ")
                        : "none"}
                    </div>
                    <div style={detailStyle}>
                      Extra:{" "}
                      {item.extra_admins.length > 0
                        ? item.extra_admins.join(", ")
                        : "none"}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </section>
    );
  };

  return (
    <div style={pageStyle}>
      <div style={heroStyle}>
        <div>
          <h1 style={titleStyle}>Meraki Admin Governance</h1>
          <p style={subtitleStyle}>
            Store the Meraki admin policy here, then let the reusable workflows
            read it. Workflow parameters stay for one-off overrides, but the
            persistent policy lives in Bifrost config.
          </p>
        </div>
        <button
          style={secondaryButtonStyle}
          onClick={() => void policyQuery.refetch()}
          disabled={policyQuery.isLoading}
        >
          Refresh Policy
        </button>
      </div>

      <section style={sectionStyle}>
        <h2 style={sectionTitleStyle}>Configuration</h2>
        {policyQuery.error && <p style={errorStyle}>{policyQuery.error}</p>}
        <div style={fieldGridStyle}>
          <label style={fieldStyle}>
            <span style={labelStyle}>Customer Org Exclusions</span>
            <textarea
              style={textareaStyle}
              rows={5}
              value={customerExclusions}
              onChange={(event) => setCustomerExclusions(event.target.value)}
            />
          </label>
          <label style={fieldStyle}>
            <span style={labelStyle}>Procurement License Orgs</span>
            <textarea
              style={textareaStyle}
              rows={3}
              value={procurementOrgs}
              onChange={(event) => setProcurementOrgs(event.target.value)}
            />
          </label>
          <label style={fieldStyle}>
            <span style={labelStyle}>Procurement Allowed Admins</span>
            <textarea
              style={textareaStyle}
              rows={3}
              value={procurementAdmins}
              onChange={(event) => setProcurementAdmins(event.target.value)}
            />
          </label>
        </div>
        <div style={actionRowStyle}>
          <button style={buttonStyle} onClick={() => void handleSave()} disabled={savePolicy.isLoading}>
            {savePolicy.isLoading ? "Saving..." : "Save Policy"}
          </button>
          {saveMessage && <span style={successStyle}>{saveMessage}</span>}
        </div>
      </section>

      <div style={auditGridStyle}>
        {renderAudit("Baseline Audit", auditBaseline)}
        {renderAudit("Procurement Audit", auditProcurement)}
      </div>
    </div>
  );
}

const pageStyle: Record<string, string | number> = {
  minHeight: "100%",
  padding: "32px",
  background:
    "linear-gradient(180deg, rgba(246,248,252,1) 0%, rgba(232,238,247,1) 100%)",
  color: "#152033",
  fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
};

const heroStyle: Record<string, string | number> = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: "16px",
  marginBottom: "24px",
};

const titleStyle = { margin: 0, fontSize: "32px", fontWeight: 700 };
const subtitleStyle = {
  margin: "8px 0 0 0",
  maxWidth: "780px",
  color: "#42526b",
  lineHeight: 1.5,
};

const sectionStyle: Record<string, string | number> = {
  background: "rgba(255,255,255,0.9)",
  border: "1px solid rgba(21,32,51,0.08)",
  borderRadius: "18px",
  padding: "20px",
  boxShadow: "0 8px 24px rgba(21,32,51,0.05)",
};

const sectionHeaderStyle: Record<string, string | number> = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: "12px",
  marginBottom: "12px",
};

const sectionTitleStyle = { margin: 0, fontSize: "20px", fontWeight: 600 };
const fieldGridStyle: Record<string, string | number> = {
  display: "grid",
  gap: "16px",
  marginTop: "16px",
};
const fieldStyle = { display: "grid", gap: "8px" };
const labelStyle = { fontSize: "14px", fontWeight: 600 };
const textareaStyle: Record<string, string | number> = {
  width: "100%",
  borderRadius: "12px",
  border: "1px solid rgba(21,32,51,0.12)",
  padding: "12px 14px",
  fontSize: "14px",
  lineHeight: 1.45,
  resize: "vertical",
  background: "#fbfcfe",
  color: "#152033",
};
const actionRowStyle: Record<string, string | number> = {
  display: "flex",
  alignItems: "center",
  gap: "12px",
  marginTop: "16px",
};
const buttonStyle: Record<string, string | number> = {
  border: "none",
  borderRadius: "999px",
  background: "#0c6cf2",
  color: "white",
  padding: "10px 16px",
  fontWeight: 600,
  cursor: "pointer",
};
const secondaryButtonStyle: Record<string, string | number> = {
  ...buttonStyle,
  background: "#152033",
};
const successStyle = { color: "#0f7b46", fontWeight: 600 };
const errorStyle = { color: "#b42318", margin: "8px 0 0 0" };
const mutedStyle = { color: "#5d6b82", margin: 0 };
const auditGridStyle: Record<string, string | number> = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
  gap: "20px",
  marginTop: "24px",
};
const resultBlockStyle = { display: "grid", gap: "12px" };
const summaryStyle = { margin: 0 };
const tableStyle = { display: "grid", gap: "10px" };
const rowStyle: Record<string, string | number> = {
  border: "1px solid rgba(21,32,51,0.08)",
  borderRadius: "12px",
  padding: "12px 14px",
  background: "#ffffff",
};
const orgNameStyle = { fontWeight: 600, marginBottom: "6px" };
const detailStyle = { color: "#42526b", fontSize: "14px", lineHeight: 1.4 };
