"""Rename the existing monitor channel without replacing its ID/history."""
def ensure_monitor_team(api,credentials):
 channels=credentials['channels'];old=channels.get('관제팀') or channels.get('관제')
 if old:
  channel,_=api('GET','/channels/'+old['id'],token=credentials['token'])
 else:
  channel,_=api('GET','/teams/'+credentials['team']['id']+'/channels/name/monitor',token=credentials['token'])
 if channel['display_name']!='관제팀':
  channel,_=api('PUT','/channels/'+channel['id']+'/patch',{'display_name':'관제팀'},credentials['token'])
 channels.pop('관제',None);channels['관제팀']=channel
 return channel
