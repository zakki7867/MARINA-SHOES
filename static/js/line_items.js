function addFormsetRow(prefix, containerId, emptyTemplateId) {
  const totalFormsInput = document.getElementById(`id_${prefix}-TOTAL_FORMS`);
  const container = document.getElementById(containerId);
  const template = document.getElementById(emptyTemplateId);
  if (!totalFormsInput || !container || !template) return;

  const idx = parseInt(totalFormsInput.value, 10);
  const html = template.innerHTML.replace(/__prefix__/g, idx);
  const wrapper = document.createElement("tr");
  wrapper.className = "line-item-row border-b border-slate-100";
  wrapper.innerHTML = html;
  container.appendChild(wrapper);
  totalFormsInput.value = idx + 1;
}
