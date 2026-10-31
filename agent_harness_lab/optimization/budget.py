"""One parent budget with an untouchable final reserve and idempotent charges."""
from dataclasses import dataclass, field
import math


@dataclass
class Budget:
    ceiling: float
    reserve: float
    events: list = field(default_factory=list)

    def __post_init__(self):
        if not all(type(v) in (int,float) and math.isfinite(v) for v in (self.ceiling,self.reserve)) or not 0 <= self.reserve <= self.ceiling:
            raise ValueError('invalid parent ceiling/reserve')
        seen=set()
        for item in self.events:
            if item['event_id'] in seen or not math.isfinite(item['amount']) or item['amount'] < 0:
                raise ValueError('invalid persisted budget')
            seen.add(item['event_id'])
        if self.spent > self.ceiling:
            raise ValueError('persisted budget exceeds ceiling')

    @property
    def spent(self):
        return sum(item['amount'] for item in self.events)

    def consume(self, kind, amount, event_id, final=False):
        if type(amount) not in (int,float) or not math.isfinite(amount) or amount < 0 or not event_id:
            raise ValueError('invalid budget event')
        for item in self.events:
            if item['event_id'] == event_id:
                if (item['kind'],item['amount'],item['final']) != (kind,amount,final):
                    raise ValueError('conflicting duplicate budget event')
                return True
        limit=self.ceiling if final else self.ceiling-self.reserve
        if self.spent+amount > limit:
            return False
        self.events.append(dict(kind=kind,amount=amount,event_id=event_id,final=final))
        return True
