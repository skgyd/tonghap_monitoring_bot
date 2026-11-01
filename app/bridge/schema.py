import uuid
from datetime import datetime, timezone

SCHEMA = {
 'incidents': ['incident_id','source','source_event_id','job_name','message','detected_at','last_seen_at','workflow_status','source_status','assignee_id','completed_by','completed_at','channel_id','revision','capture_status','last_reminded_at','missing_count','unmapped'],
 'deliveries': ['delivery_id','incident_id','channel_id','role','post_id','status','attempt_count','next_retry_at','last_error','sent_revision','file_ids','updated_at'],
 'actions': ['action_id','incident_id','actor_id','action_type','created_at','note'],
 'captures': ['capture_id','incident_id','file_path','captured_at','capture_type'],
 'routes': ['route_id','source','target_key','channel_id','enabled'],
 'collector_state': ['collector_id','last_success_at','health_status','message'],
 'notifications': ['notification_id','incident_id','created_at','kind','message'],
 'activity_mirrors': ['mirror_id','incident_id','source_delivery_id','target_delivery_id','source_post_id','target_post_id','kind','status','source_updated_at','content_hash','file_ids','source_reactions','attempt_count','next_retry_at','last_error','updated_at'],
 'meta': ['key','value']
}

def now(): return datetime.now(timezone.utc).isoformat(timespec='microseconds')
def uid(): return str(uuid.uuid4())

def row(table,**values): return {c:values.get(c,'') for c in SCHEMA[table]}
