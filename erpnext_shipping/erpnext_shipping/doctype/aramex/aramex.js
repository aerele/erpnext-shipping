frappe.ui.form.on("Aramex", {
	default_company: function (frm) {
		setTimeout(() => {
			console.log("Default company changed:", frm.doc.default_company);
			if (frm.doc.default_company) {
				frappe.call({
					method: "erpnext_shipping.erpnext_shipping.doctype.aramex.aramex.get_other_defaults",
					args: { current: frm.doc.name },
					callback: function (r) {
						if (r.message && r.message.length) {
							let default_names = r.message.join(", ");
							frappe.confirm(
								`The following Aramex records are currently default: ${default_names}. 
                                 Do you want to set this record as default?`,
								function () {
									frappe.call({
										method: "erpnext_shipping.erpnext_shipping.doctype.aramex.aramex.set_default_company",
										args: { current: frm.doc.name },
										callback: function () {
											frm.reload_doc();
										},
									});
								},
								function () {
									frm.set_value("default_company", 0);
								}
							);
						}
					},
				});
			}
		}, 50);
	},
});
