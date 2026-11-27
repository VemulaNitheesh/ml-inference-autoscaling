from locust import LoadTestShape
 
STAGES = [(180, 4), (240, 40), (420, 4), (480, 40), (660, 4), (720, 40), (900, 4)]  # (end_second, users)
SPAWN_RATE = 50
 
 
class BurstShape(LoadTestShape):
    def tick(self):
        t = self.get_run_time()
        for end, users in STAGES:
            if t < end:
                return users, SPAWN_RATE
        return None
