output "application_ids" {
  description = "Application ID per slug."
  value       = { for slug, a in authentik_application.this : slug => a.id }
}

output "application_uuids" {
  description = "Application UUID per slug (the identifier policy bindings target)."
  value       = { for slug, a in authentik_application.this : slug => a.uuid }
}

output "group_ids" {
  description = "Group ID per `groups` key."
  value       = { for key, g in authentik_group.this : key => g.id }
}

output "oauth2_provider_ids" {
  description = "OAuth2 provider ID per `oauth2_providers` key."
  value       = { for key, p in authentik_provider_oauth2.this : key => p.id }
}

output "oauth2_client_ids" {
  description = "Client ID per `oauth2_providers` key — what each application's OIDC config must use."
  value       = { for key, p in authentik_provider_oauth2.this : key => p.client_id }
}

output "proxy_provider_ids" {
  description = "Proxy provider ID per `proxy_providers` key."
  value       = { for key, p in authentik_provider_proxy.this : key => p.id }
}

output "saml_provider_ids" {
  description = "SAML provider ID per `saml_providers` key."
  value       = { for key, p in authentik_provider_saml.this : key => p.id }
}

output "custom_scope_mapping_ids" {
  description = "Scope-mapping ID per `custom_scope_mappings` key — the identifier a disaster-recovery re-import needs."
  value       = { for key, m in authentik_property_mapping_provider_scope.custom : key => m.id }
}

output "policy_binding_ids" {
  description = "Binding UUID per `policy_bindings` key — the identifier a disaster-recovery re-import needs."
  value       = { for key, b in authentik_policy_binding.this : key => b.id }
}
