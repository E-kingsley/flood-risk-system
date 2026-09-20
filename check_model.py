import json

with open('data_collection/models/flood_risk_xgb_model.json') as f:
    data = json.load(f)

print(data.get('learner', {}).get('feature_names'))
print(data.get('learner', {}).get('feature_types'))