import r2pipe
import binascii
import threading

try:
    import frida
except ImportError:
    pass

class R2API:
    """ API for interacting with r2 through r2pipe """

    def __init__(self, r2p=None, filename=None, flags=["-2"], pcode=False):
        self.r2p = r2p
        if r2p == None:
            #self.r2p = r2pipe.open(filename, flags=flags)
            if filename == None:
                self.r2p = r2pipe.open()
            else:
                self.r2p = r2pipe.open(filename, flags=flags)

        if pcode:
            self.r2p.cmd("pdga")
            
        self.instruction_cache = {}
        self.permission_cache  = {}
        # default stack size
        self.stack_size = 0x20000
        self.cache_num = 64
        self.sleep = 0.1
        self.ccs = {}

        self.register_info = None
        self.get_register_info()
        self.info = None
        self.get_info()

        info = self.r2p.cmdj("ij")
        try:
            self.frida = info["core"]["file"].startswith("frida:")
        except:
            self.frida = False

        self.debug = self.r2p.cmd("di") not in (None, "")

        self.frida_sess = None
        self.script = None
        self.frida_sess_init()

        self.segments = None
        self.get_segment_info()

    def frida_sess_init(self):
        if self.frida:
            info = self.r2p.cmdj("ij")
            self.pid = int(self.r2p.cmd("\\dp"))

            if "/usb/" in info["core"]["file"]:
                self.device = frida.get_usb_device()
            else:
                self.device = frida.get_local_device()

            for dev in frida.enumerate_devices():
                if dev.id in info["core"]["file"]:
                    self.device = dev

            self.frida_sess = self.device.attach(self.pid)

    def get_info(self):
        if self.info == None:
            self.info = self.r2p.cmdj("iaj")

        return self.info

    def get_register_info(self):
        if self.register_info == None:
            self.register_info = self.r2p.cmdj("aerpj")
            self.all_regs = [r["name"] for r in self.register_info["reg_info"]]

        return self.register_info

    def get_segment_info(self):
        if self.segments == None:
            self.segments = []

            if self.frida:
                segments = self.r2p.cmdj("\dmj")

                for seg in segments:
                    self.add_segment(
                        "",
                        seg["size"],
                        seg["protection"],
                        int(seg["base"], 16)
                    )

            elif self.debug:
                segments = self.r2p.cmdj("dmj")

                for seg in segments:
                    self.add_segment(
                        seg["name"],
                        seg["addr_end"]-seg["addr"],
                        seg["perm"],
                        seg["addr"]
                    )
            else:
                segments = self.r2p.cmdj("iSj")

                for seg in segments:
                    self.add_segment(
                        seg["name"],
                        seg["vsize"],
                        seg["perm"][1:],
                        seg["vaddr"]
                    )

        return self.segments

    def add_segment(self, name, size, perm, addr):
        self.segments.append({
            "name": name,
            "size": size,
            "perm": perm,
            "addr": addr
        })
        
    def get_permissions(self, addr):
        if addr in self.permission_cache:
            return self.permission_cache[addr]

        for seg in self.segments:
            if addr >= seg["addr"] and addr < (seg["addr"] + seg["size"]):
                self.permission_cache[addr] = seg["perm"]
                return seg["perm"]

        return "----"
        
    def get_reg_value(self, reg):
        return int(self.r2p.cmd("aer %s" % reg), 16)

    def set_reg_value(self, reg, value):
        self.r2p.cmd("aer %s=%d" % (reg, value))

    def get_gpr_values(self):
        return self.r2p.cmdj("aerj")

    def seek(self, addr):
        self.r2p.cmd("s %s" % str(addr))

    def step(self, sz):
        self.r2p.cmd("s+ %d" % sz)

    def disass(self, addr=None, instrs=1):
        if addr in self.instruction_cache and instrs == 1:
            return self.instruction_cache[addr]

        cmd = "pdj %d" % max(instrs, self.cache_num)
        if addr != None:
            cmd += " @ %d" % addr

        result = self.r2p.cmdj(cmd)
        for instr in result:
            self.instruction_cache[instr["offset"]] = instr

        if instrs == 1:
            return result[0]

        return result[:instrs]

    def disass_function(self, addr=None):
        cmd = "pdfj"
        if addr != None:
            cmd += " @ %d" % addr

        result = self.r2p.cmdj(cmd)
        for instr in result["ops"]:
            self.instruction_cache[instr["offset"]] = instr

        return result["ops"]

    def read(self, addr, length):
        return self.r2p.cmdj("xj %d @ %d" % (length, addr))

    def write(self, addr, value, length=None, fill="0"):
        val = value
        if type(value) == int:
            if length == None:
                length = int(self.info["info"]["bits"]/8)

            return self.r2p.cmd("wv%d %d @ %d" % (length, value, addr))

        elif type(value) == bytes:
            val = binascii.hexlify(value).decode()

        if length != None:
            val = val.rjust(length, str(fill))

        cmd = "wx %s @ %d" % (val, addr)
        #print(cmd)
        return self.r2p.cmd(cmd)

    # theres no arj all function to get all the regs as json so i made this
    # i should just make a pull request for r2
    def get_all_registers(self, thread=None):
        reg_dict = {}

        for reg in self.all_regs:
            val_str = self.r2p.cmd("aer %s" % reg).strip().split(" = ")[-1]   
            if val_str[:2] != "0x":
                val_str = "0x0"

            reg_dict[reg] = int(val_str, 16)

        return reg_dict

    def init_vm(self, thread=None):
        if not self.frida:
            self.r2p.cmd("aeim")
            stack = int(self.r2p.cmd("ar SP"), 16)
            self.add_segment(
                "stack",
                self.stack_size,
                "rw-",
                stack-int(self.stack_size/2)
            )
        else:
            reg_dict = {}
            reg_dicts = self.r2p.cmdj("\drj")

            for rd in reg_dicts:
                if thread == None or thread == rd["id"]:
                    reg_dict = rd["context"]
                    break

            # .\dr* should do this but doesn't always work
            for reg in reg_dict:
                self.set_reg_value(reg, int(reg_dict[reg], 16))

        self.r2p.cmd("aei; aeip") # set PC

    def frida_init(self, addr):
        self.disass(addr) # cache unhooked instrs

        reg_dict = self.frida_context(addr)

        # .\dr* should do this but doesn't always work
        for reg in reg_dict:
            self.set_reg_value(reg, int(reg_dict[reg], 16))

    def emu(self, instr):
        self.r2p.cmd("ae %s" % instr["esil"])

    def emustep(self):
        self.r2p.cmd("aes")

    def analyze_function(self, func):
        self.r2p.cmdj("af @  %s" % str(func))

    def function_info(self, func):
        self.analyze_function(func)
        return self.r2p.cmdj("afij @ %s" % str(func))[0]

    # get calling convention for sims
    def calling_convention(self, func):
        if func in self.ccs:
            return self.ccs[func]
        else:
            self.ccs[func] = self.r2p.cmdj("afcrj @ %s" % str(func))
            return self.ccs[func]

    def get_address(self, func):
        if not self.frida:
            return self.r2p.cmdj("pdj 1 @ %s" % str(func))[0]["offset"]
        else:
            return int(self.r2p.cmd("\isa %s" % str(func)), 16)

    def analyze(self, level=3): # level 7 solves ctfs automatically
        self.r2p.cmd("a"*level)

    def frida_continue(self):
        if not self.frida:
            return 
            
        self.r2p.cmd("\\dc")

        if self.script != None:
            self.script.post({"type": "continue"})
            self.script.unload()
            self.script = None

    def frida_context(self, addr):        
        # super jank
        func = '''send(this.context);recv('continue',function(){}).wait()''' 
        script_data = '''Interceptor.attach(ptr('0x%x'),function(){%s})''' \
             % (addr, func)
        
        self.script = self.frida_sess.create_script(script_data)

        context = {}
        event = threading.Event()
        def on_context(message, data):
            if message["type"] == "send":
                context.update(message["payload"])
                event.set()

        self.script.on('message', on_context)
        self.script.load()

        event.wait()
        return context