output "zone_id" {
  description = "Cloudflare zone ID."
  value       = data.cloudflare_zone.this.id
}

output "zone_name" {
  description = "Zone name."
  value       = var.zone_name
}

output "zone_status" {
  description = "Zone status as reported by Cloudflare."
  value       = data.cloudflare_zone.this.status
}

output "name_servers" {
  description = "Cloudflare nameservers assigned to the zone."
  value       = data.cloudflare_zone.this.name_servers
}

output "record_ids" {
  description = "Record ID per `records` key, across all four lifecycle classes."
  value = merge(
    { for key, r in cloudflare_record.this : key => r.id },
    { for key, r in cloudflare_record.protected : key => r.id },
    { for key, r in cloudflare_record.external_content : key => r.id },
    { for key, r in cloudflare_record.protected_external_content : key => r.id },
  )
}

output "record_hostnames" {
  description = "Fully-qualified hostname per `records` key."
  value = merge(
    { for key, r in cloudflare_record.this : key => r.hostname },
    { for key, r in cloudflare_record.protected : key => r.hostname },
    { for key, r in cloudflare_record.external_content : key => r.hostname },
    { for key, r in cloudflare_record.protected_external_content : key => r.hostname },
  )
}
